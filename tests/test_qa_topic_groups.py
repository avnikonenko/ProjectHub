"""Real SQLite Q&A saves and real topic validation, with model replies simulated."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import qa
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import GenerationRevision, Project, ProjectQA


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(id=1, name='Selected'), Project(id=2, name='Other')])
        db.commit()
    item = {'question': 'How is storage organized?', 'answer': 'SQLite stores project records.',
            'source_ids': ['source:1'], 'variants': [
                {'answer': 'An older design used files.', 'source_ids': ['source:2']}]}
    original = deepcopy(item)
    monkeypatch.setattr(qa, 'archive_history', lambda *args: ([{
        'text': 'evidence', 'sources': [{'id': 'source:1'}]}], []))
    monkeypatch.setattr(qa, 'generate_items', lambda *args, **kwargs: [
        (item, {identifier: {'id': identifier, 'text': identifier}
                for identifier in ('source:1', 'source:2')})])
    monkeypatch.setattr(qa, 'consolidate_items', lambda *args: [deepcopy(item)])
    yield engine, item, original
    engine.dispose()


class TopicGenerator:
    generation_account = 'test-account'
    generation_model = 'test-model'
    generation_effort = 'low'

    def __init__(self, invalid=False):
        self.calls = []
        self.invalid = invalid

    def generate_brief(self, prompt, schema):
        payload = json.loads(prompt.rsplit('\n', 1)[1])
        self.calls.append(payload)
        records = payload['records']
        assert schema['properties']['assignments']['minItems'] == len(records)
        if self.invalid:
            return json.dumps({'groups': [], 'assignments': [
                {'item_id': 'foreign-question', 'group_ids': []}]})
        return json.dumps({'groups': [{'id': 'storage', 'label': 'Storage',
                                      'description': 'Project storage choices.'}],
                           'assignments': [{'item_id': record['id'], 'group_ids': ['storage']}
                                           for record in records]})


def test_question_identity_is_normalized_and_legacy_reads_do_not_write(prepared):
    engine, item, original = prepared
    assert qa.question_id('  HOW is storage organized？！ ') == qa.question_id(item['question'])
    with Session(engine) as db:
        db.add(ProjectQA(project_id=1, generated_at='legacy', items=[item], sources=[], generation={}))
        db.commit()
        saved = qa.read_qa(db, 1)
        assert saved['items'][0]['id'] == qa.question_id(item['question'])
        assert saved['topic_groups'] == []
        assert saved['topic_grouping'] == {}
        assert not db.dirty
        db.expire_all()
        assert db.get(ProjectQA, 1).items == [original]
        assert qa.read_qa(db, 2) is None


def test_refresh_groups_actual_records_and_reuses_previous_memberships(prepared):
    engine, item, original = prepared
    generator = TopicGenerator()
    saved = qa.refresh_qa(engine, 1, generator)
    identifier = qa.question_id(item['question'])
    assert len(generator.calls) == 1
    assert saved['items'] == [{**original, 'id': identifier}]
    assert item == original
    assert saved['topic_groups'][0]['item_ids'] == [identifier]
    assert saved['generation']['topic_groups'] == saved['topic_groups']
    record = generator.calls[0]['records'][0]
    assert record['id'] == identifier
    assert all(text in record['statement'] for text in
               (item['question'], item['answer'], item['variants'][0]['answer']))
    assert saved['topic_grouping']['ai_requests'] == 1
    assert identifier in saved['topic_grouping']['record_fingerprints']
    again = qa.refresh_qa(engine, 1, generator)
    assert len(generator.calls) == 1
    assert again['topic_grouping']['reused_items'] == 1
    with Session(engine) as db:
        assert qa.read_qa(db, 1) == again
        assert qa.read_qa(db, 2) is None
        revisions = list(db.scalars(select(GenerationRevision).where(
            GenerationRevision.project_id == 1)))
        assert len(revisions) == 2


def test_grouping_failure_preserves_saved_qa_and_revisions(prepared):
    engine, item, _ = prepared
    saved = qa.refresh_qa(engine, 1, TopicGenerator())
    item['answer'] = 'Changed answer requires new topic classification.'
    with pytest.raises(CodexError, match='foreign'):
        qa.refresh_qa(engine, 1, TopicGenerator(invalid=True))
    with Session(engine) as db:
        assert qa.read_qa(db, 1) == saved
        assert len(list(db.scalars(select(GenerationRevision)))) == 1


@pytest.mark.parametrize('fail_commit', [False, True])
def test_delete_prunes_grouping_atomically_and_preserves_revisions(prepared, monkeypatch, fail_commit):
    from backend.app.integrations.codex.router import delete_qa_item

    engine, item, _ = prepared
    first = qa.question_id(item['question'])
    second_item = {'question': 'How are imports handled?', 'answer': 'Imported data is untrusted.',
                   'source_ids': ['source:2']}
    second = qa.question_id(second_item['question'])
    groups = [
        {'id': 'storage', 'label': 'Storage', 'description': 'Storage choices.', 'item_ids': [first]},
        {'id': 'data', 'label': 'Data', 'description': 'Data flows.', 'item_ids': [first, second]},
    ]
    metadata = {'project_id': 1, 'record_fingerprints': {first: 'first-hash', second: 'second-hash'},
                'taxonomy': [{key: group[key] for key in ('id', 'label', 'description')} for group in groups]}
    snapshot = {'generated_at': 'saved', 'items': [item, second_item],
                'sources': [{'id': 'source:1'}, {'id': 'source:2'}],
                'generation': {'topic_groups': groups, 'topic_grouping': metadata, 'model': 'unchanged'}}
    with Session(engine) as db:
        db.add(ProjectQA(project_id=1, **deepcopy(snapshot)))
        db.add(GenerationRevision(project_id=1, kind='qa', generated_at='saved', snapshot=deepcopy(snapshot)))
        db.commit()
        if fail_commit:
            def broken_commit():
                raise RuntimeError('Simulated database failure')
            monkeypatch.setattr(db, 'commit', broken_commit)
            with pytest.raises(RuntimeError, match='database failure'):
                delete_qa_item(1, db, item['question'], 'saved')
            db.rollback()
        else:
            result = delete_qa_item(1, db, item['question'], 'saved')
            assert result['topic_groups'] == [{**groups[1], 'item_ids': [second]}]
            assert result['topic_grouping']['record_fingerprints'] == {second: 'second-hash'}
            assert result['topic_grouping']['taxonomy'] == [metadata['taxonomy'][1]]
            assert result['generation']['model'] == 'unchanged'
            assert result['sources'] == [{'id': 'source:2'}]
            emptied = delete_qa_item(1, db, second_item['question'], 'saved')
            assert emptied['items'] == []
            assert emptied['topic_groups'] == []
            assert emptied['topic_grouping']['record_fingerprints'] == {}
            assert emptied['topic_grouping']['taxonomy'] == []
    with Session(engine) as db:
        assert db.scalar(select(GenerationRevision)).snapshot == snapshot
        if fail_commit:
            row = db.get(ProjectQA, 1)
            assert row.items == snapshot['items']
            assert row.generation == snapshot['generation']
