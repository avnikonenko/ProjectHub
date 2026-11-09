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
        allowed = [source['id'] for source in data['sources']]
        for section in output_schema['properties']['sections']['properties'].values():
            assert section['items']['properties']['source_ids']['items']['enum'] == allowed
        self.input = data
        ref = 'foreign' if self.foreign_citation else data['sources'][-1]['id']
        diagram = data['sources'][0]['id']
        sections = {name: [] for name in SECTION_NAMES}
        metadata = next(source for source in data['sources'] if 'Project objective and scope' in source['origins'])
        sections['overview'] = [{'text': 'Project A provides local analysis.', 'source_ids': [metadata['id']], 'classification': 'reported'}]
        sections['decision_candidates'] = [{'text': 'Possible decision', 'source_ids': [ref], 'classification': 'reported'}]
        sections['architecture'] = [{'text': 'Service stores records in a database', 'source_ids': [diagram], 'classification': 'reported'}]
        validation = next(source for source in data['sources'] if 'Available validation evidence' in source['origins'])
        sections['validation'] = [{'text': validation['text'], 'source_ids': [validation['id']], 'classification': 'verified'}]
        sections['current_status'] = [{'text': 'Implementation is complete', 'source_ids': [diagram], 'classification': 'verified'}]
        sections['next_steps'] = [{'text': 'Check the migration', 'source_ids': [diagram], 'classification': 'inferred'}]
        if getattr(self, 'extra_validation', False):
            metadata = next(source for source in data['sources'] if 'Project objective and scope' in source['origins'])
            sections['validation'] = [
                {'text': 'A reported check', 'source_ids': [diagram], 'classification': 'reported'},
                {'text': 'A second reported check', 'source_ids': [metadata['id']], 'classification': 'reported'},
            ]
        if getattr(self, 'specific_validation', False):
            sections['validation'] = [{
                'text': 'The discussion reports migration tests passed; no independent log is saved.',
                'source_ids': [ref, validation['id']], 'classification': 'reported',
            }]
        if getattr(self, 'invalid_status', False):
            sections['next_steps'][0]['classification'] = 'guaranteed'
        if getattr(self, 'missing_status', False):
            sections['current_status'] = []
        if self.reported_change:
            sections['changes'] = [{'text': self.reported_change, 'source_ids': [ref], 'classification': 'reported'}]
            sections['since_last_brief'] = [{'text': self.reported_change, 'source_ids': [ref], 'classification': 'reported'}]
        if hasattr(self, 'overview_text'):
            sections['overview'][0]['text'] = self.overview_text
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
    assert len(excerpts) == 5  # The two identical session prefixes now share one evidence summary.
    assert 'Project A' in excerpts[2]
    assert 'Support reproducible local analysis' in excerpts[2]
    assert all('Conclusion from saved history' in text for text in excerpts[4:])
    assert 'Service --> Database' in excerpts[0] and 'Accepted evidence' in excerpts[1]
    assert not any(value in '\n'.join(excerpts)
                   for value in ('Foreign diagram', 'Tentative idea', 'Private decision'))
    assert any(source.get('shared_sessions') for source in brief['sources'])
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
    with pytest.raises(CodexError, match='foreign sources|History unavailable') as exc:
        refresh_brief(project_db, 1, codex)
    from backend.app.integrations.codex import progress
    assert progress.read(1, 'brief')['error'] == str(exc.value)
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
    assert observation['text'] == 'No verified test or validation results.'
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


def test_status_does_not_save_sources_for_discarded_validation_points(project_db):
    codex = FakeCodex()
    codex.extra_validation = True
    status = refresh_brief(project_db, 1, codex)
    assert [entry['text'] for entry in status['sections']['validation']][1:] == ['A reported check']
    assert 'evidence:validation' in {source['id'] for source in status['sources']}
    assert 'A second reported check' not in [entry['text'] for entry in status['sections']['validation']]


def test_overview_requires_purpose_and_reported_architecture():
    from backend.app.integrations.codex.brief import validate_sections

    sections = {name: [] for name in SECTION_NAMES}
    sections['current_status'] = [{'text': 'Current work', 'source_ids': ['source'],
                                   'classification': 'reported'}]
    raw = json.dumps({'sections': sections, 'project_map': {'nodes': [], 'edges': []}})
    with pytest.raises(CodexError, match='overview or reported architecture'):
        validate_sections(raw, [{'id': 'source'}])


def test_overview_keeps_recent_discussion_and_code_available_directly(project_db, monkeypatch):
    from backend.app import project_evidence

    original_collect = project_evidence.collect_project_evidence

    def collect(engine, project_id):
        result = original_collect(engine, project_id)
        result['sources'].append({
            'id': 'session:1:turn:latest', 'label': 'Latest project discussion',
            'text': 'The ownership conflict was fixed; work is now on focused project overviews.',
            'classification': 'reported', 'recent': True, 'session_id': 1, 'turn_id': 'latest',
        })
        return result

    monkeypatch.setattr(project_evidence, 'collect_project_evidence', collect)
    monkeypatch.setattr(project_evidence, 'collect_repository_code', lambda path: {
        'sources': [{'id': 'repository:code:main.py', 'label': 'main.py', 'path': 'main.py',
                     'text': 'app = FastAPI()', 'classification': 'verified'}],
        'coverage': ['Inspected one source excerpt.'],
    })
    with Session(project_db) as db:
        db.get(Project, 1).repository_path = '/unavailable-for-metadata'
        db.commit()
    codex = FakeCodex()
    refresh_brief(project_db, 1, codex)
    assert any(source['kind'] == 'repository_code' and 'FastAPI' in source['text']
               for source in codex.input['sources'])
    assert any(source['kind'] == 'recent_original_excerpt' and 'ownership conflict was fixed' in source['text']
               for source in codex.input['sources'])
    assert 'Inspected one source excerpt.' in codex.input['coverage']


def test_specific_reported_validation_is_not_removed_with_generic_duplicates(project_db):
    codex = FakeCodex()
    codex.specific_validation = True
    status = refresh_brief(project_db, 1, codex)
    assert len(status['sections']['validation']) == 2
    assert status['sections']['validation'][1]['text'].startswith('The discussion reports migration tests')


def test_overview_has_room_for_complete_sentences_and_rejects_cut_off_text(project_db):
    codex = FakeCodex()
    codex.overview_text = ('ProjectHub helps people return to long projects with a usable summary of conversations, '
                          'notes, current work and the reasoning behind past choices. '
                          'The workspace connects locally saved discussions and repository context, makes supporting '
                          'passages searchable, and lets users request a focused overview before continuing their '
                          'work with source references, relevant technical context and visible unresolved questions, '
                          'without reading the entire conversation history again.')
    codex.overview_text += ' '  # Whitespace must not affect validation.
    assert len(codex.overview_text) > 420
    saved = refresh_brief(project_db, 1, codex)
    assert saved['sections']['overview'][0]['text'] == codex.overview_text
    codex.overview_text = 'ProjectHub helps people continue their work and can, '
    with pytest.raises(CodexError, match='incomplete sentence'):
        refresh_brief(project_db, 1, codex)
    with Session(project_db) as db:
        assert read_brief(db, 1) == saved
