"""Q&A snapshot persistence, archive batching, and citation validation."""
import json

import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import qa
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import Answer, Decision, Project, Note, Question, Task, GenerationRevision
from sqlalchemy import select


def test_qa_consolidates_batches_and_preserves_snapshot_on_foreign_variant(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(name='One'), Project(name='Two')])
        db.commit()
    evidence = [{'text': f'Decision {i}', 'sources': [{'id': f'turn:{i}', 'text': f'Original {i}'}]}
                for i in range(15)]
    monkeypatch.setattr(qa, 'archive_history', lambda *args: (evidence, []))

    class Generator:
        bad = False
        batches = 0
        consolidations = 0

        def generate_brief(self, prompt, schema):
            batch = json.loads(prompt.split('\n', 1)[1])
            if 'Build a compact project reference' in prompt:
                assert self.batches >= 2
                assert schema['properties']['items']['maxItems'] == 2
                assert 'at most 2 distinct entries' in prompt
                self.consolidations += 1
                if self.consolidations == 3:
                    assert 'Correct the previous attempt' in prompt
                assert all('[99]' not in item['answer'] for item in batch)
                by_question = {item['question']: item['source_ids'] for item in batch}
                assert set(by_question) == {
                    'How does One store data?', 'What does One do?', 'What is the purpose of One?',
                    'Which projects does One target?',
                }
                return json.dumps({'items': [{
                    'question': 'What does One do?',
                    'answer': 'One tracks context for any project locally. [99]',
                    'source_ids': [*by_question['What does One do?'], *by_question['What is the purpose of One?']],
                    'variants': [{
                        'answer': 'A separate discussion says it targets only software projects.',
                        'source_ids': ['foreign' if self.bad else by_question['Which projects does One target?'][0]],
                    }],
                }, {
                    'question': 'How does One store data?',
                    'answer': 'One stores records in SQLite.',
                    'source_ids': by_question['How does One store data?'],
                }]})
            self.batches += 1
            allowed = schema['properties']['items']['items']['properties']['source_ids']['items']['enum']
            assert allowed == [item['id'] for item in batch]
            if batch[0]['text'] == 'Decision 3':
                return json.dumps({'items': [
                    {'question': 'How does One store data?', 'answer': 'In SQLite.', 'source_ids': ['2']},
                    {'question': 'What does One do?', 'answer': 'One tracks context for any project. [99]',
                     'source_ids': ['1']},
                ]})
            assert batch[0]['text'] == 'Decision 0'
            return json.dumps({'items': [
                {'question': 'What is the purpose of One?', 'answer': 'It works locally.', 'source_ids': ['1']},
                {'question': 'Which projects does One target?', 'answer': 'Only software projects.', 'source_ids': ['2']},
            ]})

    manager = Generator()
    saved = qa.refresh_qa(engine, 1, manager, max_items=2)
    assert manager.batches == 2
    assert manager.consolidations == 1
    assert [item['question'] for item in saved['items']] == ['What does One do?', 'How does One store data?']
    assert saved['items'][0]['answer'] == 'One tracks context for any project locally.'
    assert saved['items'][0]['source_ids'] == ['turn:3', 'turn:0']
    assert saved['items'][0]['variants'][0]['source_ids'] == ['turn:1']
    assert saved['items'][1]['source_ids'] == ['turn:4']
    assert {source['id'] for source in saved['sources']} == {'turn:3', 'turn:0', 'turn:1', 'turn:4'}
    manager.bad = True
    with pytest.raises(CodexError, match='foreign evidence'):
        qa.refresh_qa(engine, 1, manager, max_items=2)
    assert manager.consolidations == 3
    with Session(engine) as db:
        assert qa.read_qa(db, 1) == saved
        assert qa.read_qa(db, 2) is None
        revisions = list(db.scalars(select(GenerationRevision)))
        assert len(revisions) == 1 and revisions[0].snapshot == saved
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
            if 'Build a compact project reference' in prompt:
                candidates = json.loads(prompt.split('\n', 1)[1])
                return json.dumps({'items': [{**candidates[0], 'variants': []}]})
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

    def rank(manager, candidates, limit):
        calls.append((len(candidates), limit))
        return candidates[:limit]

    monkeypatch.setattr(qa, '_consolidate_group', rank)
    candidates = [{'question': f'Question {i}?'} for i in range(145)]
    assert len(qa.consolidate_items(None, candidates, 15)) == 15
    assert calls[-1][1] == 15
    assert len(qa.consolidate_items(None, candidates, 120)) == 120
    assert all(size <= 72 and limit <= 72 for size, limit in calls)
