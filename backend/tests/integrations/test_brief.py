"""Brief persistence and provenance with real SQLite and a fake Codex transport."""
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex.brief import SECTION_NAMES, read_brief, refresh_brief
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import CodexSession, Decision, Project


class FakeCodex:
    def __init__(self):
        self.reads = []
        self.closed = False
        self.foreign_citation = False
        self.fail_read = False

    def _new_read_client(self):
        return self

    def close(self):
        self.closed = True

    def history(self, session, cursor, *, client):
        assert client is self
        self.reads.append((session.thread_id, cursor))
        if self.fail_read:
            raise CodexError('History unavailable')
        messages = [{'role': 'user', 'text': 'Question ' + 'x' * 5000},
                    {'role': 'assistant', 'text': 'Conclusion from saved history'}]
        return {'turns': [{'id': 'older' if cursor else 'recent', 'messages': messages}],
                'next_cursor': None if cursor else 'next'}

    def generate_brief(self, prompt, output_schema):
        assert self.closed
        self.input = json.loads(prompt.split('\n', 1)[1])
        ref = 'foreign' if self.foreign_citation else self.input['sources'][-1]['id']
        sections = {name: [] for name in SECTION_NAMES}
        sections['decision_candidates'] = [{'text': 'Possible decision', 'source_ids': [ref]}]
        return json.dumps({'sections': sections})


@pytest.fixture
def project_db(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(name='Project A'), Project(name='Project B')])
        db.flush()
        db.add_all([
            CodexSession(project_id=1, thread_id='a-first', status='linked'),
            CodexSession(project_id=1, thread_id='a-second', status='linked'),
            CodexSession(project_id=2, thread_id='foreign', status='linked'),
            Decision(project_id=1, title='Accepted evidence', status='accepted'),
            Decision(project_id=1, title='Tentative idea', status='proposed'),
            Decision(project_id=2, title='Private decision', status='accepted'),
        ])
        db.commit()
    yield engine
    engine.dispose()


def test_brief_collects_multiple_scoped_sessions_and_keeps_decision_candidates_separate(project_db):
    codex = FakeCodex()
    brief = refresh_brief(project_db, 1, codex)
    assert codex.reads == [('a-second', None), ('a-second', 'next'),
                          ('a-first', None), ('a-first', 'next')]
    excerpts = [source['text'] for source in codex.input['sources']]
    assert len(excerpts) == 5
    assert all('Conclusion from saved history' in text for text in excerpts[1:])
    assert 'Accepted evidence' in excerpts[0]
    assert 'Tentative idea' not in excerpts[0] and 'Private decision' not in excerpts[0]
    assert brief['sections']['decision_candidates']
    with Session(project_db) as db:
        assert read_brief(db, 1) == brief
        assert read_brief(db, 2) is None
        assert len(list(db.scalars(select(Decision)))) == 3


@pytest.mark.parametrize('failure', ['foreign_citation', 'fail_read'])
def test_failed_refresh_preserves_last_brief(project_db, failure):
    codex = FakeCodex()
    old = refresh_brief(project_db, 1, codex)
    setattr(codex, failure, True)
    with pytest.raises(CodexError, match='foreign sources|History unavailable'):
        refresh_brief(project_db, 1, codex)
    with Session(project_db) as db:
        assert read_brief(db, 1) == old
