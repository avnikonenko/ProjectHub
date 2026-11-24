"""Draft output-cap recovery with real caches and simulated provider responses."""
from contextlib import contextmanager
from copy import deepcopy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.generation_profiles import get_generation_profile
from backend.app.models import Project, ProjectHistoryBatch, ProjectQA
from backend.app.integrations.codex import qa
from backend.app.integrations.codex.knowledge_profiles import use_knowledge_profile


def reply(count, *, question='Question', reference='1'):
    return json.dumps({'items': [{'question': f'{question} {index}?', 'answer': 'Supported answer.',
                                 'source_ids': [reference]} for index in range(count)]})


def evidence():
    return [{'text': 'Consider shared storage.', 'sources': [
                {'id': 'original:proposal', 'label': 'Proposal', 'text': 'Consider shared storage.'}]},
            {'text': 'Yes, adopt shared storage.', 'sources': [
                {'id': 'original:acceptance', 'label': 'Acceptance', 'text': 'Yes, adopt shared storage.'}]}]


class Manager:
    generation_account = 'fixture'
    generation_model = 'fixture'
    generation_effort = 'medium'

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.children = 0

    @contextmanager
    def independent_generation(self):
        self.children += 1
        identity = self.children
        owner = self

        class Child:
            def generate_brief(self, prompt, schema):
                owner.requests.append((identity, prompt, deepcopy(schema)))
                return owner.responses.pop(0)

        yield Child()


@pytest.fixture
def engine(tmp_path):
    result, _ = initialize(tmp_path)
    with Session(result) as db:
        db.add(Project(id=1, name='One'))
        db.add(ProjectQA(project_id=1, generated_at='previous', items=[], sources=[], generation={}))
        db.commit()
    yield result
    result.dispose()


def test_oversized_draft_gets_one_fresh_retry_with_full_evidence_and_caches_only_corrected_output(engine):
    batch = evidence()
    before = deepcopy(batch)
    manager = Manager(reply(9), reply(1, question='Corrected selection', reference='2'))
    result = qa.generate_items(manager, batch, engine=engine, project_id=1)
    assert result[0][0]['question'] == 'Corrected selection 0?'
    assert result[0][0]['source_ids'] == ['original:acceptance']
    assert manager.children == 2 and len(manager.requests) == 2
    first, second = manager.requests
    assert first[0] != second[0]
    assert 'Return at most 8 question-and-answer entries' in first[1]
    assert 'previous attempt exceeded the output cap' in second[1]
    assert json.loads(first[1].split('\n', 1)[1]) == json.loads(second[1].split('\n', 1)[1])
    assert batch == before
    with Session(engine) as db:
        cached = list(db.scalars(select(ProjectHistoryBatch)))
        assert len(cached) == 1
        assert json.loads(cached[0].evidence[0]['raw'])['items'][0]['question'] == 'Corrected selection 0?'
    assert qa.generate_items(manager, batch, engine=engine, project_id=1) == result
    assert len(manager.requests) == 2


def test_small_persistent_overflow_keeps_validated_entries_in_priority_order_and_reuses_cache(engine):
    batch = evidence() * 13
    corrected = json.loads(reply(26, question='Prioritized', reference='2'))
    answer = '保存された根拠。' * 100
    for item in corrected['items']:
        item['answer'] = answer
    manager = Manager(reply(26, question='First attempt'), json.dumps(corrected, ensure_ascii=False))
    result = qa.generate_items(manager, batch, engine=engine, project_id=1)
    assert [item['question'] for item, _ in result] == [f'Prioritized {index}?' for index in range(24)]
    assert all(item['source_ids'] == ['original:acceptance'] for item, _ in result)
    assert all(item['answer'] == answer for item, _ in result)
    assert 'List entries in priority order' in manager.requests[-1][1]
    with Session(engine) as db:
        cached = list(db.scalars(select(ProjectHistoryBatch)))
        assert len(cached) == 1
        entry, = cached[0].evidence
        assert entry['overflow_trimmed'] == 2
        assert len(json.loads(entry['raw'])['items']) == 24
    assert qa.generate_items(manager, batch, engine=engine, project_id=1) == result
    assert len(manager.requests) == 2


def test_large_persistent_overflow_is_not_truncated_cached_or_published(engine, monkeypatch):
    batch = evidence()
    monkeypatch.setattr(qa, 'archive_history', lambda *args: (batch, []))
    earlier = [{'text': 'Previously completed draft.', 'sources': [
        {'id': 'original:earlier', 'text': 'Previously completed draft.'}]}]
    qa.generate_items(Manager(reply(1)), earlier, engine=engine, project_id=1)
    manager = Manager(reply(9), reply(13))
    with Session(engine) as db:
        previous = qa.read_qa(db, 1)
        previous_caches = [(row.fingerprint, deepcopy(row.evidence))
                           for row in db.scalars(select(ProjectHistoryBatch))]
    with use_knowledge_profile(get_generation_profile('quality')):
        with pytest.raises(qa.InvalidQAOutputLimit, match=r'returned 13 items; allowed 8'):
            qa.refresh_qa(engine, 1, manager)
    assert manager.children == 2 and len(manager.requests) == 2
    with Session(engine) as db:
        assert qa.read_qa(db, 1) == previous
        assert [(row.fingerprint, row.evidence) for row in db.scalars(select(ProjectHistoryBatch))] == previous_caches


def test_overflow_correction_does_not_accept_foreign_evidence(engine):
    manager = Manager(reply(9), reply(1, reference='foreign'))
    with pytest.raises(qa.InvalidQACitation, match='foreign'):
        qa.generate_items(manager, evidence(), engine=engine, project_id=1)
    assert len(manager.requests) == 2
    with Session(engine) as db:
        assert not list(db.scalars(select(ProjectHistoryBatch)))


@pytest.mark.parametrize('changes,error', [
    ({'source_ids': ['foreign']}, qa.InvalidQACitation),
    ({'answer': 'x' * 801}, qa.InvalidQAOutput),
])
def test_small_persistent_overflow_validates_items_past_the_retained_cap(engine, changes, error):
    corrected = json.loads(reply(9))
    corrected['items'][-1].update(changes)
    manager = Manager(reply(9), json.dumps(corrected))
    with pytest.raises(error):
        qa.generate_items(manager, evidence(), engine=engine, project_id=1)
    assert len(manager.requests) == 2
    with Session(engine) as db:
        assert not list(db.scalars(select(ProjectHistoryBatch)))
