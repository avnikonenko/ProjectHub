"""Q&A snapshot persistence, archive batching, and citation validation."""
import json

import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import qa
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import Project


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
            first = json.loads(prompt.split('\n', 1)[1])[0]['text']
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
    engine.dispose()
