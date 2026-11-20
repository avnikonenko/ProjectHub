"""ID-only ranking preserves validated answers, alternatives and canonical citations."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, ProjectHistoryBatch
from backend.app.integrations.codex import archive, progress, qa
from backend.app.integrations.codex.protocol import CodexError, JSONFormattingError


class Manager:
    generation_account = 'fixture'
    generation_provider = 'claude'
    generation_model = 'sonnet'
    generation_effort = 'low'

    def __init__(self, response=None):
        self.response = response
        self.calls = []

    def generate_brief(self, prompt, schema):
        self.calls.append((prompt, deepcopy(schema)))
        if isinstance(self.response, Exception):
            raise self.response
        if self.response is not None:
            return json.dumps(self.response)
        payload = json.loads(prompt.split('\n', 1)[1])
        return json.dumps({'selected_ids': [item['id'] for item in payload][:
            schema['properties']['selected_ids']['maxItems']]})


def candidate(index, **changes):
    return {'question': f'Question {index}?', 'answer': f'Validated answer {index}.',
            'source_ids': [f'original:{index}'], **changes}


@pytest.fixture
def engine(tmp_path):
    result, _ = initialize(tmp_path)
    with Session(result) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')])
        db.commit()
    yield result
    result.dispose()


def test_selected_ids_reorder_complete_original_answers_without_semantic_merging():
    candidates = [candidate(1), candidate(2), candidate(3, question='question 1?', answer='Another recorded answer.')]
    before = deepcopy(candidates)
    manager = Manager({'selected_ids': ['q2', 'q1']})
    result = qa._consolidate_group(manager, candidates, 2)
    assert result[0] == candidates[1]
    assert result[1] == {**candidates[0], 'variants': [
        {'answer': 'Another recorded answer.', 'source_ids': ['original:3']} ]}
    payload = json.loads(manager.calls[0][0].split('\n', 1)[1])
    assert [item['id'] for item in payload] == ['q1', 'q2']
    assert payload[0]['recorded_alternative_count'] == 1
    assert all('source_ids' not in item and 'variants' not in item for item in payload)
    assert candidates == before


def test_many_alternatives_are_retained_but_do_not_expand_model_projection():
    candidates = [candidate(0)] + [candidate(index, question='Question 0?', answer='B' * 790 + str(index))
                                    for index in range(1, 201)]
    manager = Manager({'selected_ids': ['q1']})
    result = qa._consolidate_group(manager, candidates, 1)
    assert len(result[0]['variants']) == 200
    payload = json.loads(manager.calls[0][0].split('\n', 1)[1])
    assert payload[0]['recorded_alternative_count'] == 200
    assert len(json.dumps(payload)) < 500
    assert result[0]['variants'][-1]['source_ids'] == ['original:200']


def test_selection_cache_binds_canonical_references_and_project_and_keeps_old_cache(engine):
    candidates = [candidate(0), candidate(1, question='Question 0?')]
    with Session(engine) as db:
        db.add(ProjectHistoryBatch(project_id=1, fingerprint='legacy-consolidation', evidence=[{'raw': 'legacy'}]))
        db.commit()
    manager = Manager()
    first = qa._consolidate_group(manager, candidates, 1, engine=engine, project_id=1)
    assert qa._consolidate_group(manager, deepcopy(candidates), 1, engine=engine, project_id=1) == first
    assert len(manager.calls) == 1
    candidates[1]['source_ids'] = ['original:changed']
    changed = qa._consolidate_group(manager, candidates, 1, engine=engine, project_id=1)
    assert changed[0]['variants'][0]['source_ids'] == ['original:changed']
    qa._consolidate_group(manager, candidates, 1, engine=engine, project_id=2)
    assert len(manager.calls) == 3
    with Session(engine) as db:
        assert db.get(ProjectHistoryBatch, (1, 'legacy-consolidation')).evidence == [{'raw': 'legacy'}]
        assert len(list(db.scalars(select(ProjectHistoryBatch)))) == 4


def test_empty_intermediate_selections_can_recover_on_the_next_refresh(engine):
    candidates = [candidate(index) for index in range(100)]
    manager = Manager({'selected_ids': []})
    progress.begin(1, 'qa')
    try:
        with archive.archive_worker_scope(1):
            result = qa.consolidate_items(manager, candidates, 3, 1, engine=engine)
        diagnostics = progress.read(1, 'qa')['diagnostics']
        assert result == candidates[:3]
        assert diagnostics['qa_consolidate_retries'] == 2
        assert diagnostics['qa_rank_retries'] == 1
        assert diagnostics['qa_consolidate_fallback_batches'] == 2
        assert diagnostics['qa_rank_fallback_batches'] == 1
        assert len(manager.calls) == 6
    finally:
        progress.finish(1)
    manager.response = None
    progress.begin(1, 'qa')
    try:
        with archive.archive_worker_scope(1):
            assert qa.consolidate_items(manager, candidates, 3, 1, engine=engine) == result
        assert len(manager.calls) == 9
        assert progress.read(1, 'qa')['diagnostics']['qa_rank_retries'] == 0
    finally:
        progress.finish(1)


def test_successful_ranking_replaces_a_matching_legacy_fallback(engine):
    candidates = [candidate(0), candidate(1)]
    initial = Manager({'selected_ids': ['q1']})
    assert qa._consolidate_group(initial, candidates, 1, engine=engine, project_id=1) == candidates[:1]
    with Session(engine) as db:
        db.scalar(select(ProjectHistoryBatch)).evidence = [{'fallback': True}]
        db.commit()
    recovered = Manager({'selected_ids': ['q2']})
    assert qa._consolidate_group(recovered, candidates, 1, engine=engine, project_id=1) == candidates[1:]
    assert len(recovered.calls) == 1
    assert qa._consolidate_group(recovered, candidates, 1, engine=engine, project_id=1) == candidates[1:]
    assert len(recovered.calls) == 1


@pytest.mark.parametrize('has_candidates', [True, False])
def test_cached_refresh_keeps_the_observed_model_and_effort(engine, monkeypatch, has_candidates):
    evidence = [{'text': 'Project information', 'sources': [{'id': 'turn:1', 'text': 'Project information'}]}]
    monkeypatch.setattr(qa, 'archive_history', lambda *args: (evidence, []))
    monkeypatch.setattr(qa, 'conversation_overviews', lambda *args: (evidence, []))

    class CodexDefaults:
        def __init__(self):
            self.calls = 0

        def generate_brief(self, prompt, schema):
            self.calls += 1
            self.generation_metadata = {'account': 'Local fixture', 'model': 'observed-model', 'effort': 'high'}
            if 'selected_ids' in schema['properties']:
                return '{"selected_ids":["q1"]}'
            items = [{'question': 'Which storage?', 'answer': 'SQLite.', 'source_ids': ['1']}] if has_candidates else []
            return json.dumps({'items': items})

    first_manager = CodexDefaults()
    saved = qa.refresh_qa(engine, 1, first_manager)
    assert first_manager.calls == (2 if has_candidates else 1)
    assert bool(saved['items']) is has_candidates
    assert saved['generation']['model'] == 'observed-model'
    assert saved['generation']['effort'] == 'high'
    replay_manager = CodexDefaults()
    replay = qa.refresh_qa(engine, 1, replay_manager)
    assert replay_manager.calls == 0
    assert replay['items'] == saved['items'] and replay['sources'] == saved['sources']
    assert replay['generation'] == saved['generation']


def test_persistent_formatting_failure_falls_back_but_transport_failure_does_not(engine):
    manager = Manager(JSONFormattingError('Formatting failed'))
    progress.begin(1, 'qa')
    try:
        with archive.archive_worker_scope(1):
            assert qa.consolidate_items(manager, [candidate(0)], 1, 1, engine=engine) == [candidate(0)]
        diagnostics = progress.read(1, 'qa')['diagnostics']
        assert len(manager.calls) == 2
        assert diagnostics['qa_rank_retries'] == diagnostics['qa_rank_fallback_batches'] == 1
    finally:
        progress.finish(1)
    failed = Manager(CodexError('Connection lost'))
    with pytest.raises(CodexError, match='Connection lost'):
        qa._consolidate_group(failed, [candidate(1)], 1, engine=engine, project_id=1)
    assert len(failed.calls) == 1


@pytest.mark.parametrize('question,answer', [('???', 'Good.'), ('...', 'Good.'),
                                          ('\ud800', 'Good.'), ('Question?', '\ud800')])
def test_drafts_reject_meaningless_questions_and_lone_surrogates(question, answer):
    raw = json.dumps({'items': [{'question': question, 'answer': answer, 'source_ids': ['1']}]})
    with pytest.raises(qa.InvalidQAOutput):
        qa._validate_items(raw, {'1': {'sources': [{'id': 'original:one'}]}})


@pytest.mark.parametrize('question', ['???', '\ud800'])
def test_legacy_unsafe_draft_repairs_only_its_cache_and_replays_without_failure(engine, question):
    def evidence(identifier):
        return [{'text': f'Original {identifier}.', 'sources': [
            {'id': identifier, 'text': f'Original {identifier}.'}]}]

    valid = {'items': [{'question': 'Which storage?', 'answer': 'SQLite.', 'source_ids': ['1']}]}
    original = Manager(valid)
    qa.generate_items(original, evidence('first'), engine=engine, project_id=1)
    with Session(engine) as db:
        first = db.scalar(select(ProjectHistoryBatch))
        first_key = first.fingerprint
    qa.generate_items(original, evidence('unrelated'), engine=engine, project_id=1)
    unsafe = {'items': [{'question': question, 'answer': 'SQLite.', 'source_ids': ['1']}]}
    with Session(engine) as db:
        db.get(ProjectHistoryBatch, (1, first_key)).evidence = [{'raw': json.dumps(unsafe)}]
        db.commit()
    repaired = Manager(valid)
    expected = qa.generate_items(repaired, evidence('first'), engine=engine, project_id=1)
    assert len(repaired.calls) == 1
    assert qa.generate_items(repaired, evidence('first'), engine=engine, project_id=1) == expected
    assert len(repaired.calls) == 1
    qa.generate_items(original, evidence('unrelated'), engine=engine, project_id=1)
    assert len(original.calls) == 2  # The unrelated valid cache is unchanged.
    with Session(engine) as db:
        assert db.get(ProjectHistoryBatch, (1, first_key)).evidence == [{'raw': json.dumps(unsafe)}]
        assert len(list(db.scalars(select(ProjectHistoryBatch)))) == 3
