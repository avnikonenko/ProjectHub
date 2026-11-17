"""Real SQLite/HTTP checks for scoped complete exports and bounded readers."""
import gc
import json
import subprocess
import sqlite3
import tracemalloc

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.app.models import (GenerationRevision, Note, Project, ProjectBrief,
                                ProjectHistoryBatch, ProjectKnowledge, ProjectQA, ProjectScheme)
from backend.app.project_export import project_export_response


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    app = create_app(tmp_path / 'data')
    with Session(app.state.engine) as db:
        db.add_all([Project(id=1, name='Local'), Project(id=2, name='Foreign')])
        db.commit()
    with TestClient(app) as client:
        yield app, client
    app.state.engine.dispose()


def test_export_includes_saved_ai_and_cache_records_without_foreign_data(state):
    app, client = state
    with Session(app.state.engine) as db:
        for pid in (1, 2):
            db.add(ProjectKnowledge(project_id=pid, snapshot={'publication_id': f'pub-{pid}'}))
            db.add(ProjectBrief(project_id=pid, generated_at='now', sections={}, sources=[], coverage=[],
                                project_state={'publication_id': f'pub-{pid}'}))
            db.add(ProjectQA(project_id=pid, generated_at='now', items=[{'question': f'q-{pid}'}], sources=[]))
            db.add(ProjectScheme(project_id=pid, snapshot={'project_id': pid}))
            db.add(ProjectHistoryBatch(project_id=pid, fingerprint=f'cache-{pid}', evidence=[{'id': f'source-{pid}'}]))
            db.add(GenerationRevision(project_id=pid, kind='knowledge', generated_at='now', snapshot={'project_id': pid}))
        db.commit()
    response = client.get('/api/projects/1/export')
    assert response.status_code == 200
    data = response.json()
    assert data['schema_version'] == 1
    assert data['knowledge']['snapshot']['publication_id'] == 'pub-1'
    assert data['brief']['project_state']['publication_id'] == 'pub-1'
    assert data['qa']['items'][0]['question'] == 'q-1'
    assert data['scheme']['snapshot']['project_id'] == 1
    assert data['history_batches'][0]['fingerprint'] == 'cache-1'
    assert len(data['generation_revisions']) == 1
    assert 'pub-2' not in response.text and 'source-2' not in response.text
    assert data['history_turns'] == [] and data['history_index'] == []
    assert client.get('/api/projects/999/export').status_code == 404


def test_audit_defaults_stay_complete_and_optional_pages_are_scoped(state):
    _, client = state
    for pid in (1, 2):
        for i in range(5):
            assert client.post(f'/api/projects/{pid}/notes', json={'title': f'n-{i}', 'content': 'original'}).status_code == 201
    all_rows = client.get('/api/projects/1/audit').json()
    assert len(all_rows) == 5
    assert all(row['project_id'] == 1 and row['after']['content'] == 'original' for row in all_rows)
    assert client.get('/api/projects/1/audit?offset=2&limit=2').json() == all_rows[2:4]
    assert client.get('/api/projects/1/audit?offset=5&limit=2').json() == []
    assert client.get('/api/projects/1/audit?limit=1001').status_code == 422
    assert client.get('/api/projects/999/audit').status_code == 404


def test_legacy_scheme_with_non_utf8_omission_still_loads_and_exports(state):
    app, client = state
    unsupported = {'path': 'src/\udcff.py', 'reason': 'no_usable_excerpt'}
    readable = {'path': 'src/café.py', 'reason': 'source_output_budget'}
    omissions = {'total': 2, 'files': [unsupported, readable]}
    snapshot = {'generated_at': 'then', 'coverage': {'file_omissions': omissions}}
    with Session(app.state.engine) as db:
        db.add(ProjectScheme(project_id=1, snapshot=snapshot))
        revision = GenerationRevision(project_id=1, kind='scheme', generated_at='then', snapshot=snapshot)
        db.add(revision)
        db.flush()
        revision_id = revision.id
        db.commit()
    for suffix in ('scheme', f'revisions/{revision_id}'):
        response = client.get(f'/api/projects/1/codex/{suffix}')
        assert response.status_code == 200
        assert response.json()['snapshot']['coverage']['file_omissions'] == {'total': 2, 'files': [readable]}
    response = client.get('/api/projects/1/export')
    assert response.status_code == 200
    assert response.json()['scheme']['snapshot'] == snapshot
    assert response.json()['generation_revisions'][0]['snapshot'] == snapshot


def test_export_spools_large_records_without_retaining_the_whole_history(state):
    app, _ = state
    with Session(app.state.engine) as db:
        db.add_all([Note(project_id=1, title=f'Large {i}', content='x' * 200_000) for i in range(40)])
        db.commit()
    gc.collect()
    tracemalloc.start()
    try:
        with Session(app.state.engine) as db:
            response = project_export_response(db, 1)
        _, peak = tracemalloc.get_traced_memory()
        assert int(response.headers['content-length']) > 8_000_000
        assert peak < 4_000_000
        # The response owns an unlinked temporary file; close even if not consumed.
        response.background.func()
    finally:
        tracemalloc.stop()


def test_completed_export_does_not_hold_a_database_lock_during_download(state):
    app, _ = state
    with Session(app.state.engine) as db:
        response = project_export_response(db, 1)
        try:
            with sqlite3.connect(app.state.data_dir / 'projecthub.sqlite3', timeout=.1) as writer:
                writer.execute("UPDATE projects SET name='Concurrent edit' WHERE id=1")
                writer.commit()
        finally:
            response.background.func()


def test_summary_and_review_skip_unrelated_note_and_audit_content(state):
    app, client = state
    client.post('/api/projects/1/notes', json={'title': 'Large source', 'kind': 'document', 'content': 'unrelated transcript ' * 10000})
    client.post('/api/projects/1/notes', json={'title': 'Limit', 'kind': 'limitation', 'content': 'Recorded limitation'})
    statements = []
    def capture(connection, cursor, sql, parameters, context, many):
        statements.append((sql.lower(), parameters))
    event.listen(app.state.engine, 'before_cursor_execute', capture)
    try:
        summary = client.get('/api/projects/1/summary').json()
        review = client.get('/api/projects/1/review').json()
    finally:
        event.remove(app.state.engine, 'before_cursor_execute', capture)
    assert summary['sections']['known_limitations'] == ['Recorded limitation']
    assert review['findings'][0]['detail'] == 'Recorded limitation'
    assert all('from audit_events' not in sql and 'from codex_sessions' not in sql for sql, _ in statements)
    reads = [(sql, parameters) for sql, parameters in statements if 'from notes' in sql]
    assert reads and all('notes.kind = ?' in sql and 'limitation' in parameters for sql, parameters in reads)


def test_repository_dashboard_uses_bounded_git_reader(state, tmp_path, monkeypatch):
    from backend.app import project_evidence
    _, client = state
    repo = tmp_path / 'repo'
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    assert client.patch('/api/projects/1', json={'repository_path': str(repo)}).status_code == 200
    calls = []
    def bounded(*args, **kwargs):
        calls.append(args)
        raise project_evidence.EvidenceReadError('synthetic output limit')
    monkeypatch.setattr(project_evidence, '_git', bounded)
    response = client.get('/api/projects/1/repository')
    assert response.status_code == 503
    assert calls and 'byte/time limit' in response.json()['detail']
