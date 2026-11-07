"""Brief persistence and provenance with real SQLite and a fake Codex transport."""
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex.brief import SECTION_NAMES, read_brief, refresh_brief, note_evidence
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import CodexSession, Decision, Project, Note


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

    def history(self, session, cursor, *, client, archive):
        assert client is self and archive
        self.reads.append((session.thread_id, cursor))
        if self.fail_read:
            raise CodexError('History unavailable')
        messages = [{'role': 'user', 'text': 'Question ' + 'x' * 5000},
                    {'role': 'assistant', 'text': 'Conclusion from saved history'}]
        return {'turns': [{'id': 'recent' if cursor else 'older', 'status': 'completed', 'messages': messages}],
                'next_cursor': None if cursor else 'next'}

    def generate_brief(self, prompt, output_schema):
        data = json.loads(prompt.split('\n', 1)[1])
        if 'evidence' in output_schema['properties']:
            refs = [source['id'] for source in data['sources'] if 'Conclusion from saved history' in source['text']]
            return json.dumps({'evidence': [{'text': 'Conclusion from saved history', 'source_ids': refs}]})
        assert self.closed
        self.input = data
        ref = 'foreign' if self.foreign_citation else data['sources'][-1]['id']
        diagram = data['sources'][0]['id']
        sections = {name: [] for name in SECTION_NAMES}
        sections['decision_candidates'] = [{'text': 'Possible decision', 'source_ids': [ref]}]
        sections['architecture'] = [{'text': 'Service stores records in a database', 'source_ids': [diagram]}]
        graph = {'nodes': [{'id': key, 'label': key.title(), 'source_ids': [diagram]}
                           for key in ('service', 'database')],
                 'edges': [{'from': 'service', 'to': 'database', 'label': 'stores records',
                            'inferred': False, 'source_ids': [diagram]}]}
        return json.dumps({'sections': sections, 'project_map': graph})


@pytest.fixture
def project_db(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(name='Project A', architecture_mermaid='flowchart LR\nService --> Database'),
                    Project(name='Project B', architecture_mermaid='Foreign diagram')])
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
    assert codex.reads == [('a-first', None), ('a-first', 'next'),
                          ('a-second', None), ('a-second', 'next')]
    excerpts = [source['text'] for source in codex.input['sources']]
    assert len(excerpts) == 4
    assert all('Conclusion from saved history' in text for text in excerpts[2:])
    assert 'Service --> Database' in excerpts[0] and 'Accepted evidence' in excerpts[1]
    assert not any(value in '\n'.join(excerpts)
                   for value in ('Foreign diagram', 'Tentative idea', 'Private decision'))
    assert brief['sections']['decision_candidates']
    assert brief['project_map']['edges'][0]['source_ids'] == ['architecture:1']
    assert brief['sections']['architecture'][0]['source_ids'] == ['architecture:1']
    assert all(not ref.startswith('history:') for ref in brief['sections']['decision_candidates'][0]['source_ids'])
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


def test_note_evidence_includes_all_parts_and_enforces_ownership(project_db):
    with Session(project_db) as db:
        content = 'Long note ' * 600
        db.add_all([Note(project_id=1, title='Manual note', content=content),
                    Note(project_id=1, title='Agent finding', content='Tentative result', kind='ai_note'),
                    Note(project_id=2, title='Foreign note', content='Private')])
        db.commit()
        evidence = list(note_evidence(db, 1))
    assert len(evidence) > 2
    assert not any('Foreign' in item['text'] or 'Private' in item['text'] for item in evidence)
    first = ''.join(item['text'] for item in evidence if item['sources'][0]['note_id'] == 1)
    assert content in first
    assert evidence[-1]['sources'][0]['kind'] == 'ai_note'
    assert 'AI note' in evidence[-1]['sources'][0]['label']
