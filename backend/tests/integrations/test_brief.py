"""Brief persistence and provenance with real SQLite and a fake Codex transport."""
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex.brief import SECTION_NAMES, read_brief, refresh_brief, note_evidence
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import CodexSession, Decision, GenerationRevision, Project, Note


class FakeCodex:
    def __init__(self):
        self.reads = []
        self.closed = False
        self.foreign_citation = False
        self.fail_read = False
        self.reported_change = ''

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
        sections['decision_candidates'] = [{'text': 'Possible decision', 'source_ids': [ref], 'classification': 'reported'}]
        sections['architecture'] = [{'text': 'Service stores records in a database', 'source_ids': [diagram], 'classification': 'reported'}]
        validation = next(source for source in data['sources'] if 'Available validation evidence' in source['origins'])
        sections['validation'] = [{'text': validation['text'], 'source_ids': [validation['id']], 'classification': 'verified'}]
        sections['current_status'] = [{'text': 'Implementation is complete', 'source_ids': [diagram], 'classification': 'verified'}]
        sections['next_steps'] = [{'text': 'Check the migration', 'source_ids': [diagram], 'classification': 'inferred'}]
        if getattr(self, 'invalid_status', False):
            sections['next_steps'][0]['classification'] = 'guaranteed'
        if getattr(self, 'missing_status', False):
            sections['current_status'] = []
        if self.reported_change:
            sections['changes'] = [{'text': self.reported_change, 'source_ids': [ref], 'classification': 'reported'}]
            sections['since_last_brief'] = [{'text': self.reported_change, 'source_ids': [ref], 'classification': 'reported'}]
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
    with Session(project_db) as db:
        db.get(Project, 1).goals = 'Support reproducible local analysis'
        db.commit()
    codex = FakeCodex()
    brief = refresh_brief(project_db, 1, codex)
    assert codex.reads == [('a-first', None), ('a-first', 'next'),
                          ('a-second', None), ('a-second', 'next')]
    excerpts = [source['text'] for source in codex.input['sources']]
    assert len(excerpts) == 6
    assert 'Project A' in excerpts[2]
    assert 'Support reproducible local analysis' in excerpts[2]
    assert all('Conclusion from saved history' in text for text in excerpts[4:])
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


def test_brief_compares_latest_success_and_saves_cited_changes(project_db):
    codex = FakeCodex()
    codex.reported_change = 'The database migration started.'
    first = refresh_brief(project_db, 1, codex)
    assert codex.input['previous_brief'] is None
    assert not first['sections']['since_last_brief']
    assert first['sections']['changes'][0]['text'] == codex.reported_change

    codex.reported_change = 'The database migration finished.'
    second = refresh_brief(project_db, 1, codex)
    baseline = codex.input['previous_brief']
    assert baseline['generated_at'] == first['generated_at']
    assert baseline['sections']['changes'] == ['The database migration started.']
    delta, = second['sections']['since_last_brief']
    assert delta['text'] == codex.reported_change
    assert set(delta['source_ids']) <= {source['id'] for source in second['sources']}
    assert not second['sections']['changes']

    # Repeating the previous update with different punctuation/case is not a new change.
    codex.reported_change = 'The DATABASE migration finished!'
    third = refresh_brief(project_db, 1, codex)
    assert not third['sections']['since_last_brief']
    with Session(project_db) as db:
        assert read_brief(db, 1) == third
        snapshots = list(db.scalars(select(GenerationRevision.snapshot).order_by(GenerationRevision.id)))
        assert snapshots == [first, second, third]


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


def test_successful_brief_saves_evidence_baseline_and_revision(project_db):
    from backend.app.project_evidence import collect_project_evidence

    codex = FakeCodex()
    brief = refresh_brief(project_db, 1, codex)
    baseline = brief['project_state']['fingerprint']
    reads = list(codex.reads)
    assert collect_project_evidence(project_db, 1)['fingerprint'] == baseline
    assert codex.reads == reads
    with Session(project_db) as db:
        latest = db.scalar(select(GenerationRevision).where(
            GenerationRevision.project_id == 1, GenerationRevision.kind == 'brief',
        ).order_by(GenerationRevision.id.desc()))
        assert latest.snapshot['project_state']['fingerprint'] == baseline
        db.add(Note(project_id=1, title='New evidence', content='A newly reported limitation.'))
        db.commit()
    assert collect_project_evidence(project_db, 1)['fingerprint'] != baseline
    codex.fail_read = True
    with pytest.raises(CodexError):
        refresh_brief(project_db, 1, codex)
    with Session(project_db) as db:
        assert read_brief(db, 1) == brief


def test_structured_status_certifies_observations_and_preserves_last_success(project_db):
    codex = FakeCodex()
    status = refresh_brief(project_db, 1, codex)
    assert status['sections']['current_status'][0]['classification'] == 'inferred'
    assert status['sections']['next_steps'][0]['classification'] == 'inferred'
    observation, = status['sections']['validation']
    assert observation['classification'] == 'verified'
    assert observation['source_ids'] == ['evidence:validation']
    assert 'No independently recorded test execution' in observation['text']
    codex.invalid_status = True
    with pytest.raises(CodexError, match='invalid evidence classification'):
        refresh_brief(project_db, 1, codex)
    with Session(project_db) as db:
        assert read_brief(db, 1) == status
        assert list(db.scalars(select(GenerationRevision.snapshot))) == [status]
    codex.invalid_status = False
    codex.missing_status = True
    with pytest.raises(CodexError, match='omitted the current status'):
        refresh_brief(project_db, 1, codex)
    with Session(project_db) as db:
        assert read_brief(db, 1) == status
