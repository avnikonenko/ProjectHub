"""Real SQLite/HTTP checks for scoped complete exports and bounded readers."""
import asyncio
import gc
from hashlib import sha256
from io import BytesIO
import json
import subprocess
import sqlite3
import tracemalloc
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from backend.app.main import create_app
from backend.app.models import (CodexSession, GenerationRevision, Note, Project, ProjectBrief,
                                ProjectHistoryBatch, ProjectKnowledge, ProjectQA, ProjectScheme, Task)
from backend.app.project_export import _ExportResponse, project_archive_response, project_export_response


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


def test_project_archive_includes_readable_views_and_project_owned_sources(state):
    app, client = state
    original = '{"title":"Planning","messages":[{"role":"user","content":"Use CSV."}]}'
    source = {'filename': 'planning.json', 'content': original,
              'source_url': 'https://claude.ai/share/00000000-0000-4000-8000-000000000001'}
    preview = client.post('/api/projects/1/conversations/preview', json=source).json()
    saved = client.post('/api/projects/1/conversations/import',
                        json={**source, 'fingerprint': preview['fingerprint'], 'confirm_partial': True})
    assert saved.status_code == 201, saved.text
    conversation_id = saved.json()['id']
    document = client.post('/api/projects/1/documents', files={'file': ('checklist.md', b'# Pack kit\n')}).json()
    foreign = client.post('/api/projects/2/documents', files={'file': ('foreign.txt', b'foreign body')}).json()
    broken = client.post('/api/projects/1/documents', files={'file': ('previous.txt', b'Local note text')}).json()
    upload_dir = app.state.data_dir / 'uploads'
    unsafe = upload_dir / broken['reference']
    unsafe.unlink()
    unsafe.symlink_to(upload_dir / foreign['reference'])
    client.post('/api/projects/1/notes', json={'title': 'Editable reference', 'kind': 'document',
                                            'reference': foreign['reference']}).raise_for_status()
    with Session(app.state.engine) as db:
        db.add_all([CodexSession(id=11, project_id=1, thread_id='local thread', title='Local session'),
                    CodexSession(id=12, project_id=2, thread_id='foreign thread', title='Foreign session'),
                    Task(project_id=1, title='Review checklist', description='Check CSV'),
                    Note(project_id=1, title='Packing note', content='Bring spare pens.'),
                    ProjectBrief(project_id=1, generated_at='now', coverage=[], sources=[],
                                 sections={'overview': [{'text': 'Workshop overview', 'source_ids': []}]}),
                    ProjectQA(project_id=1, generated_at='now', sources=[],
                              items=[{'question': 'What is imported?', 'answer': 'CSV.'}]),
                    ProjectScheme(project_id=1, snapshot={'generated_at': 'now', 'architecture': {}}),
                    ProjectKnowledge(project_id=1, snapshot={'publication_id': 'current',
                        'summary': {'sections': {'overview': [{'text': 'Specific knowledge summary'}]}},
                        'items': [{'id': 'choice', 'statement': 'Use CSV.', 'design_status': 'AGREED'}]}),
                    GenerationRevision(project_id=1, kind='parameters', generated_at='now',
                                       snapshot={'items': [{'name': 'MAX_INPUT', 'default_value': '8'}]})])
        db.flush()
        db.execute(text('INSERT INTO codex_history_turns '
                        '(project_id,session_id,session_title,turn_id,status,messages,content) '
                        "VALUES (1,:sid,'Session','one','completed',:messages,:body)"), [
                            {'sid': 11, 'messages': '[{"role":"user","text":"Save a transcript."}]', 'body': 'Local turn'},
                            {'sid': 12, 'messages': '[]', 'body': 'foreign history'},
                        ])
        db.commit()

    request = client.get('/api/projects/1/export.zip')
    assert request.status_code == 200
    assert request.headers['content-type'] == 'application/zip'
    assert request.headers['content-disposition'] == 'attachment; filename="Local-project.zip"'
    assert int(request.headers['content-length']) == len(request.content)
    with ZipFile(BytesIO(request.content)) as archive:
        names = set(archive.namelist())
        assert archive.testzip() is None
        assert {'project.json', 'index.md', 'overview.md', 'knowledge.md', 'scheme.md',
                'ai-qa.md', 'implementation-choices.md', 'todos.md', 'notes.md', 'sources/index.md'} <= names
        data = json.loads(archive.read('project.json'))
        copy = data['conversation_import_sources'][0]
        assert copy['content_hash'] == sha256(original.encode()).hexdigest()
        assert archive.read(copy['archive_path']) == original.encode()
        assert 'payload_base64' not in copy
        paths = data['source_paths']
        assert archive.read(paths['uploads'][str(document['id'])]) == b'# Pack kit\n'
        assert 'Use CSV.' in archive.read(paths['notes'][str(conversation_id)]).decode()
        assert 'user:\nSave a transcript.' in archive.read(paths['sessions']['11']).decode()
        assert archive.read(paths['uploads'][str(broken['id'])]) == b'Local note text'
        assert len(paths['uploads']) == 2 and '12' not in paths['sessions']
        assert [row['session_id'] for row in data['history_turns']] == [11]
        assert data['parameters']['items'][0]['name'] == 'MAX_INPUT'
        assert data['knowledge']['snapshot']['publication_id'] == 'current'
        assert 'Review checklist' in archive.read('todos.md').decode()
        assert 'Bring spare pens.' in archive.read('notes.md').decode()
        assert 'Local note text' in archive.read('notes.md').decode()
        assert 'Workshop overview' in archive.read('overview.md').decode()
        assert 'Specific knowledge summary' in archive.read('knowledge.md').decode()
        assert 'What is imported?' in archive.read('ai-qa.md').decode()
        assert r'MAX\_INPUT' in archive.read('implementation-choices.md').decode()
        assert not any(b'foreign body' in archive.read(member) or b'foreign history' in archive.read(member)
                       for member in names)
    assert client.get('/api/projects/999/export.zip').status_code == 404


def test_cross_origin_subresources_cannot_start_project_exports(state, monkeypatch):
    _, client = state
    def require_no_export(*args):
        pytest.fail('Cross-origin request started export')

    monkeypatch.setattr('backend.app.main.project_archive_response', require_no_export)
    monkeypatch.setattr('backend.app.main.project_export_response', require_no_export)
    for path in ('export.zip', 'export'):
        for site in ('cross-site', 'same-site'):
            blocked = client.get(f'/api/projects/1/{path}',
                                 headers={'Sec-Fetch-Site': site, 'Sec-Fetch-Dest': 'image'})
            assert blocked.status_code == 403


def test_download_stream_closes_temporary_file_when_asgi_send_fails():
    handle = BytesIO()
    handle.write(b'content')
    response = _ExportResponse(handle, media_type='application/zip')

    async def receive():
        pytest.fail('ASGI 2.4 streaming waits for a failed send rather than polling the receiver')

    async def send(message):
        if message['type'] == 'http.response.body':
            raise OSError('Client disconnected')

    with pytest.raises(ClientDisconnect):
        asyncio.run(response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send))
    assert handle.closed


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


@pytest.mark.parametrize('archive', [False, True])
def test_completed_export_does_not_hold_a_database_lock_during_download(state, archive):
    app, _ = state
    with Session(app.state.engine) as db:
        response = project_archive_response(db, 1) if archive else project_export_response(db, 1)
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
