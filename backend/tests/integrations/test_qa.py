"""Q&A snapshot persistence, archive batching, and citation validation."""
import json

import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import generation_attempts, qa
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import Answer, Decision, Project, Note, Question, Task, GenerationRevision
from sqlalchemy import select



def test_qa_selects_canonical_answers_and_preserves_alternatives_on_invalid_selection(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(name='One'), Project(name='Two')])
        db.commit()
    evidence = [{'text': f'Decision {i}', 'sources': [{'id': f'turn:{i}', 'text': f'Original {i}'}]}
                for i in range(15)]
    monkeypatch.setattr(qa, 'archive_history', lambda *args: (evidence, []))
    monkeypatch.setattr(qa, '_DRAFT_BATCH_ITEMS', 12)

    class Generator:
        bad = False
        batches = 0
        consolidations = 0

        def generate_brief(self, prompt, schema):
            batch = json.loads(prompt.split('\n', 1)[1])
            if 'Select and rank existing project Q&A IDs' in prompt:
                self.consolidations += 1
                assert self.batches == 2
                assert schema['properties']['selected_ids']['maxItems'] in (2, 3)
                assert all('source_ids' not in item for item in batch)
                by_question = {item['question']: item['id'] for item in batch}
                if self.bad:
                    return json.dumps({'selected_ids': ['foreign']})
                return json.dumps({'selected_ids': [by_question['What does One do?'],
                                                    by_question['How does One store data?']]})
            self.batches += 1
            if batch[0]['text'] == 'Decision 12':
                return json.dumps({'items': [
                    {'question': 'How does One store data?', 'answer': 'In SQLite.', 'source_ids': ['2']},
                    {'question': 'What does One do?', 'answer': 'One tracks context for any project. [99]', 'source_ids': ['1']},
                ]})
            return json.dumps({'items': [
                {'question': 'What is the purpose of One?', 'answer': 'It works locally.', 'source_ids': ['1']},
                {'question': 'What does One do?', 'answer': 'Only software projects.', 'source_ids': ['2']},
            ]})

    manager = Generator()
    saved = qa.refresh_qa(engine, 1, manager, max_items=2)
    with Session(engine) as db:
        assert generation_attempts.read_latest(db, 1, 'qa')['settings']['max_items'] == 2
    assert manager.batches == 2 and manager.consolidations == 1
    assert [item['question'] for item in saved['items']] == ['What does One do?', 'How does One store data?']
    assert saved['items'][0]['answer'] == 'One tracks context for any project.'
    assert saved['items'][0]['source_ids'] == ['turn:12']
    assert saved['items'][0]['variants'] == [{'answer': 'Only software projects.', 'source_ids': ['turn:1']}]
    assert saved['items'][1]['source_ids'] == ['turn:13']
    assert {source['id'] for source in saved['sources']} == {'turn:12', 'turn:1', 'turn:13'}
    manager.bad = True
    fallback = qa.refresh_qa(engine, 1, manager, max_items=3)
    assert manager.consolidations == 3 and manager.batches == 2
    assert fallback['diagnostics']['qa_rank_fallback_batches'] == 1
    assert fallback['diagnostics']['qa_rank_retries'] == 1
    assert 'Used previously validated answers' in ' '.join(fallback['coverage'])
    assert len(fallback['items']) == 3
    assert all('foreign' not in item['source_ids'] for item in fallback['items'])
    manager.bad = False
    recovered = qa.refresh_qa(engine, 1, manager, max_items=3)
    assert recovered['items'] == saved['items']
    assert recovered['diagnostics']['qa_rank_fallback_batches'] == 0
    replay = qa.refresh_qa(engine, 1, manager, max_items=3)
    assert replay['items'] == recovered['items']
    assert replay['diagnostics']['qa_rank_cache_hits'] == 1 and replay['diagnostics']['qa_rank_retries'] == 0
    assert manager.consolidations == 4
    with Session(engine) as db:
        assert qa.read_qa(db, 1) == replay and qa.read_qa(db, 2) is None
        revisions = list(db.scalars(select(GenerationRevision)))
        assert len(revisions) == 4 and any(row.snapshot == saved for row in revisions)
    engine.dispose()


def test_qa_uses_shared_discussions_accepted_records_and_architecture(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(name='One', architecture_mermaid='flowchart LR; UI --> API'),
                    Project(name='Two')])
        db.flush()
        db.add(Question(id=1, project_id=1, title='Which storage?'))
        db.flush()
        db.add_all([
            Note(project_id=1, title='Shared discussion', content='Use glass jars', kind='document',
                 reference='https://chatgpt.com/share/00000000-0000-4000-8000-000000000001'),
            Note(project_id=1, title='Backlog notes', content='NOTE BACKLOG', kind='ai_note'),
            Decision(project_id=1, title='Use SQLite', status='accepted'),
            Decision(project_id=1, title='PROPOSED ONLY', status='proposed'),
            Decision(project_id=2, title='FOREIGN DECISION', status='accepted'),
            Answer(project_id=1, question_id=1, solution='Use local storage', status='accepted'),
            Task(project_id=1, title='TASK BACKLOG'),
        ])
        db.commit()
    monkeypatch.setattr(qa, 'archive_history', lambda *args: ([], []))
    class Generator:
        generation_metadata = {'account': 'test-profile', 'model': 'test-model', 'effort': 'high'}
        def generate_brief(self, prompt, schema):
            if 'Select and rank existing project Q&A IDs' in prompt:
                candidates = json.loads(prompt.split('\n', 1)[1])
                return json.dumps({'selected_ids': [candidates[0]['id']]})
            assert 'Use glass jars' in prompt
            assert all(text in prompt for text in ('Ignore TODO lists', 'UI --> API', 'Use SQLite', 'Use local storage'))
            assert all(text not in prompt for text in ('NOTE BACKLOG', 'TASK BACKLOG', 'PROPOSED ONLY', 'FOREIGN DECISION'))
            return json.dumps({'items': [{'question': 'What containers were suggested?',
                                          'answer': 'Glass jars were suggested.', 'source_ids': ['1']}]})
    saved = qa.refresh_qa(engine, 1, Generator())
    assert saved['items'][0]['source_ids'] == ['note:1:1']
    assert saved['generation'] == Generator.generation_metadata
    with Session(engine) as db:
        assert qa.read_qa(db, 1) == saved
    engine.dispose()


def test_qa_retries_only_the_invalid_batch_and_keeps_no_partial_answers():
    class Generator:
        calls = 0

        def generate_brief(self, prompt, schema):
            self.calls += 1
            assert schema['properties']['items']['items']['properties']['source_ids']['items']['enum'] == ['1']
            items = [{'question': 'What was chosen?', 'answer': 'SQLite', 'source_ids': ['1']}]
            if self.calls == 1:
                items.append({'question': 'Unsupported?', 'answer': 'Other', 'source_ids': ['unknown']})
            else:
                assert 'previous attempt cited unavailable evidence' in prompt
            return json.dumps({'items': items})

    manager = Generator()
    result = qa.generate_items(manager, [{'text': 'SQLite', 'sources': [{'id': 'note:1'}]}])
    assert manager.calls == 2
    assert len(result) == 1
    assert result[0][0]['source_ids'] == ['note:1']


def test_qa_limit_applies_to_output_without_a_fixed_saved_ceiling(monkeypatch):
    from backend.app.integrations.codex.router import QAGenerationInput
    from pydantic import ValidationError

    assert QAGenerationInput().max_items == 25
    assert QAGenerationInput(max_items=120).max_items == 120
    for invalid in (0, -1, 1.5, True, '15'):
        with pytest.raises(ValidationError):
            QAGenerationInput(max_items=invalid)

    calls = []

    def rank(manager, candidates, limit, **kwargs):
        calls.append((len(candidates), limit))
        return candidates[:limit]

    monkeypatch.setattr(qa, '_consolidate_group', rank)
    candidates = [{'question': f'Question {i}?', 'answer': 'Validated answer.', 'source_ids': [f'source:{i}']} for i in range(145)]
    assert len(qa.consolidate_items(None, candidates, 15)) == 15
    assert calls[-1][1] == 15
    assert len(qa.consolidate_items(None, candidates, 120)) == 120
    assert all(size <= 72 and limit <= 72 for size, limit in calls)



def test_qa_draft_cache_survives_manager_restart_and_refreshes_source_metadata(tmp_path):
    from backend.app.models import ProjectHistoryBatch

    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')])
        db.commit()

    class Generator:
        calls = 0
        generation_account = 'example-codex-profile'
        generation_model = 'model-a'
        generation_effort = 'low'

        def generate_brief(self, prompt, schema):
            self.calls += 1
            return json.dumps({'items': [{'question': 'Which storage?',
                                          'answer': 'SQLite.', 'source_ids': ['1']}]})

    batch = [{'text': 'Use SQLite.', 'sources': [
        {'id': 'turn:1', 'label': 'Storage discussion', 'text': 'Original passage', 'updated_at': 'old'},
    ]}]
    first = Generator()
    qa.generate_items(first, batch, engine=engine, project_id=1)
    assert first.calls == 1
    second = Generator()
    batch[0]['sources'][0]['updated_at'] = 'new'
    result = qa.generate_items(second, batch, engine=engine, project_id=1)
    assert second.calls == 0
    assert result[0][1]['turn:1']['updated_at'] == 'new'
    qa.generate_items(second, batch, engine=engine, project_id=2)
    assert second.calls == 1  # Another project cannot reuse the cached row.
    with Session(engine) as db:
        assert len(list(db.scalars(select(ProjectHistoryBatch)))) == 2
    engine.dispose()


def test_qa_draft_cache_keys_include_generation_settings_and_evidence(tmp_path):
    from backend.app.integrations.codex.ai_instructions import use_project_instructions

    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(id=1, name='One'))
        db.commit()

    class Generator:
        calls = 0
        generation_account = 'example-codex-profile'
        generation_model = 'model-a'
        generation_effort = 'low'

        def generate_brief(self, prompt, schema):
            self.calls += 1
            return json.dumps({'items': [{'question': 'Which storage?',
                                          'answer': 'SQLite.', 'source_ids': ['1']}]})

    manager = Generator()
    batch = [{'text': 'Use SQLite.', 'sources': [{'id': 'turn:1', 'text': 'Original'}]}]
    def generate():
        qa.generate_items(manager, batch, engine=engine, project_id=1)

    with use_project_instructions('Be compact.'):
        generate()
        generate()
        assert manager.calls == 1
        for key, value in [('generation_account', 'another-account'),
                           ('generation_model', 'model-b'), ('generation_effort', 'high')]:
            setattr(manager, key, value)
            generate()
        assert manager.calls == 4
        batch[0]['text'] = 'Use SQLite locally.'
        generate()
        batch[0]['sources'][0]['id'] = 'turn:2'
        generate()
        assert manager.calls == 6
    with use_project_instructions('Include reasons.'):
        generate()
        assert manager.calls == 7
    engine.dispose()


def test_qa_invalid_drafts_are_not_cached_and_empty_success_is_cached(tmp_path):
    from backend.app.models import ProjectHistoryBatch

    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(id=1, name='One'))
        db.commit()

    class Generator:
        calls = 0
        invalid = True

        def generate_brief(self, prompt, schema):
            self.calls += 1
            if self.invalid:
                return json.dumps({'items': [{'question': 'Unsupported?', 'answer': 'Unsupported.',
                                              'source_ids': ['foreign']}]})
            return json.dumps({'items': []})

    manager = Generator()
    batch = [{'text': 'Proposed backlog only.', 'sources': [{'id': 'turn:1'}]}]
    with pytest.raises(CodexError, match='foreign evidence'):
        qa.generate_items(manager, batch, engine=engine, project_id=1)
    assert manager.calls == 2
    with Session(engine) as db:
        assert list(db.scalars(select(ProjectHistoryBatch))) == []
    manager.invalid = False
    assert qa.generate_items(manager, batch, engine=engine, project_id=1) == []
    assert qa.generate_items(manager, batch, engine=engine, project_id=1) == []
    assert manager.calls == 3
    engine.dispose()


def test_qa_draft_batches_reduce_calls_and_keep_forward_boundaries():
    evidence = [{'text': f'Fact {i}', 'sources': [{'id': f'turn:{i}'}]} for i in range(131)]
    batches = qa.draft_batches(evidence)
    assert [len(batch) for batch in batches] == [32, 32, 32, 32, 3]
    extended = qa.draft_batches([*evidence, {'text': 'New fact', 'sources': [{'id': 'turn:131'}]}])
    assert extended[:-1] == batches[:-1]
    assert [item for batch in batches for item in batch] == evidence


def test_qa_draft_batches_bound_actual_serialized_payload_and_reject_oversized_items():
    evidence = [{'text': 'α' * 5500, 'sources': [
        {'id': f'turn:{i}', 'label': 'A discussion label' * 50},
    ]} for i in range(10)]
    batches = qa.draft_batches(evidence)
    assert len(batches) > 1
    for batch in batches:
        payload = [{'id': str(i), 'text': item['text'],
                    'sources': [source.get('label', 'Saved conversation evidence')
                                for source in item['sources']]}
                   for i, item in enumerate(batch, 1)]
        assert len(json.dumps(payload, ensure_ascii=False)) <= 24_000
    with pytest.raises(CodexError, match='input limit'):
        qa.draft_batches([{'text': 'x' * 24_000, 'sources': [{'id': 'turn:large'}]}])


def test_qa_retry_reuses_completed_drafts_after_a_later_batch_fails(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(id=1, name='One'))
        db.commit()
    evidence = [{'text': f'Fact {i}', 'sources': [{'id': f'turn:{i}'}]} for i in range(33)]
    monkeypatch.setattr(qa, 'archive_history', lambda *args: (evidence, []))

    class Generator:
        fail = True
        seen = []

        def generate_brief(self, prompt, schema):
            payload = json.loads(prompt.split('\n', 1)[1])
            if 'Select and rank existing project Q&A IDs' in prompt:
                return json.dumps({'selected_ids': [payload[0]['id']]})
            first = payload[0]['text']
            self.seen.append(first)
            if self.fail and first == 'Fact 0':
                raise CodexError('Synthetic unavailable service')
            return json.dumps({'items': [{'question': f'What is {first}?',
                                          'answer': first, 'source_ids': ['1']}]})

    manager = Generator()
    with pytest.raises(CodexError, match='Synthetic unavailable'):
        qa.refresh_qa(engine, 1, manager)
    with Session(engine) as db:
        assert qa.read_qa(db, 1) is None
    manager.fail = False
    saved = qa.refresh_qa(engine, 1, manager)
    assert manager.seen == ['Fact 32', 'Fact 0', 'Fact 0']
    assert saved['items'][0]['source_ids'] == ['turn:32']
    engine.dispose()
