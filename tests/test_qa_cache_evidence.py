"""Actual SQLite draft caches; model responses are simulated, with no AI calls."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import qa
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import Project, ProjectHistoryBatch, ProjectQA


@pytest.fixture
def engine(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')])
        db.add(ProjectQA(project_id=1, generated_at='saved', items=[], sources=[],
                         generation={'saved': True}))
        db.commit()
    yield engine
    engine.dispose()


class Generator:
    calls = 0
    generation_account = 'fixture'
    generation_model = 'fixture'
    generation_effort = 'medium'

    def generate_brief(self, prompt, schema):
        self.calls += 1
        return json.dumps({'items': [{'question': 'What is the approach?',
                                     'answer': f'Draft {self.calls}.', 'source_ids': ['1']}]})


def batch():
    return [{'text': 'Unchanged derived summary', 'sources': [{
        'id': 'session:1:turn:1:part:1', 'label': 'Same conversation',
        'text': 'user: Use approach A.', 'session_id': 1, 'turn_id': '1',
    }]}]


@pytest.mark.parametrize('revision', [None, 'original-revision'])
def test_original_quote_change_invalidates_even_when_summary_id_and_revision_match(engine, revision):
    manager, evidence = Generator(), batch()
    if revision is not None:
        evidence[0]['sources'][0]['revision'] = revision
    first = qa.generate_items(manager, evidence, engine=engine, project_id=1)
    evidence[0]['sources'][0]['text'] = 'user: Use approach B instead.'
    second = qa.generate_items(manager, evidence, engine=engine, project_id=1)
    replay = qa.generate_items(manager, deepcopy(evidence), engine=engine, project_id=1)
    assert manager.calls == 2
    assert first[0][0]['answer'] == 'Draft 1.'
    assert second[0][0]['answer'] == replay[0][0]['answer'] == 'Draft 2.'
    assert next(iter(replay[0][1].values()))['text'] == 'user: Use approach B instead.'
    with Session(engine) as db:
        assert len(list(db.scalars(select(ProjectHistoryBatch)))) == 2
        assert db.get(ProjectQA, 1).generation == {'saved': True}


@pytest.mark.parametrize('field', ['revision', 'source_revision', 'content_hash', 'turn_status'])
def test_explicit_evidence_revision_invalidates_unchanged_quote(engine, field):
    manager, evidence = Generator(), batch()
    evidence[0]['sources'][0][field] = 'first'
    qa.generate_items(manager, evidence, engine=engine, project_id=1)
    evidence[0]['sources'][0][field] = 'second'
    qa.generate_items(manager, evidence, engine=engine, project_id=1)
    assert manager.calls == 2


def test_metadata_and_dictionary_order_do_not_invalidate_but_projects_remain_isolated(engine):
    manager, evidence = Generator(), batch()
    evidence[0]['sources'][0].update(updated_at='old', imported_at='old')
    qa.generate_items(manager, evidence, engine=engine, project_id=1)
    evidence[0]['sources'][0] = dict(reversed(list(evidence[0]['sources'][0].items())))
    evidence[0]['sources'][0].update(updated_at='new', imported_at='new')
    replay = qa.generate_items(manager, evidence, engine=engine, project_id=1)
    assert manager.calls == 1
    assert next(iter(replay[0][1].values()))['updated_at'] == 'new'
    qa.generate_items(manager, evidence, engine=engine, project_id=2)
    assert manager.calls == 2


@pytest.mark.parametrize('corrupt', [[], [{'raw': 'not JSON'}], [{'raw': json.dumps({
    'items': [{'question': 'Question?', 'answer': 'Answer.', 'source_ids': ['foreign']}]
})}]])
def test_cache_hits_still_validate_structure_and_citations_without_overwriting_saved_qa(engine, corrupt):
    manager, evidence = Generator(), batch()
    qa.generate_items(manager, evidence, engine=engine, project_id=1)
    with Session(engine) as db:
        cached = db.scalar(select(ProjectHistoryBatch))
        cached.evidence = corrupt
        db.commit()
    with pytest.raises(CodexError):
        qa.generate_items(manager, evidence, engine=engine, project_id=1)
    assert manager.calls == 1
    with Session(engine) as db:
        saved = db.get(ProjectQA, 1)
        assert saved.generated_at == 'saved'
        assert saved.items == []
        assert saved.generation == {'saved': True}
