"""Q&A snapshot persistence, archive batching, and citation validation."""
import json

import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import qa
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import Answer, Decision, Project, Note, Question, Task, GenerationRevision
from sqlalchemy import select


def test_qa_persists_multiple_batches_and_preserves_snapshot_on_failure(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(name='One'), Project(name='Two')])
        db.commit()
    evidence = [{'text': f'Decision {i}', 'sources': [{'id': f'turn:{i}', 'text': f'Original {i}'}]}
                for i in range(15)]
    monkeypatch.setattr(qa, 'archive_history', lambda *args: (evidence, []))

    class Generator:
        bad = False

        def generate_brief(self, prompt, schema):
            batch = json.loads(prompt.split('\n', 1)[1])
            allowed = schema['properties']['items']['items']['properties']['source_ids']['items']['enum']
            assert allowed == [item['id'] for item in batch]
            first = batch[0]['text']
            return json.dumps({'items': [{'question': f'What was {first}?', 'answer': first,
                                          'source_ids': ['unknown' if self.bad else '1']}]})

    manager = Generator()
    saved = qa.refresh_qa(engine, 1, manager)
    assert [item['source_ids'] for item in saved['items']] == [['turn:3'], ['turn:0']]
    manager.bad = True
    with pytest.raises(CodexError, match='foreign evidence'):
        qa.refresh_qa(engine, 1, manager)
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


def test_duplicate_questions_merge_sources_and_preserve_differing_answers():
    items = qa.merge_items([
        {'question': 'Why use SQLite?', 'answer': 'It runs locally.', 'source_ids': ['a']},
        {'question': ' WHY use SQLite?? ', 'answer': 'It runs locally.', 'source_ids': ['b']},
        {'question': 'Why use SQLite?', 'answer': 'Later the team chose PostgreSQL.', 'source_ids': ['c']},
        {'question': 'Why use SQLite?', 'answer': 'Later the team chose PostgreSQL.', 'source_ids': ['d']},
        {'question': 'Why use C++?', 'answer': 'Fast.', 'source_ids': ['e']},
        {'question': 'Why use C#?', 'answer': 'Managed.', 'source_ids': ['f']},
    ])
    assert len(items) == 3
    assert items[0]['source_ids'] == ['a', 'b']
    assert items[0]['variants'] == [{
        'answer': 'Later the team chose PostgreSQL.', 'source_ids': ['c', 'd'],
    }]
