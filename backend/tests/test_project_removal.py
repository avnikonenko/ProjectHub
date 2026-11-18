"""Project removal uses the real API, SQLite constraints, and full-text indexes."""

import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app import main
from backend.app.main import create_app
from backend.app.models import (
    CodexEvent, CodexSession, ContextPackage, Delivery, GenerationRevision,
    ProjectAIInstructions, ProjectBrief, ProjectGenerationAttempt, ProjectHistoryBatch,
    ProjectKnowledge, ProjectQA, ProjectScheme,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    with TestClient(create_app(tmp_path / 'data'), raise_server_exceptions=False) as client:
        yield client


def post(client, path, body, expected_status=201):
    response = client.post(path, json=body)
    assert response.status_code == expected_status, response.text
    return response.json()


def seed_codex_records(engine, project_id, thread_id, decision_id, search_term):
    with Session(engine) as db:
        session = CodexSession(project_id=project_id, thread_id=thread_id)
        db.add(session)
        db.flush()
        context = ContextPackage(project_id=project_id, session_id=session.id,
                                 content='Saved context', version='1')
        db.add(context)
        db.flush()
        db.add_all([
            Delivery(project_id=project_id, session_id=session.id, decision_id=decision_id,
                     context_id=context.id, content='Saved delivery'),
            CodexEvent(project_id=project_id, session_id=session.id, method='turn/completed', payload={}),
            ProjectBrief(project_id=project_id, generated_at='fixture', sections={}, sources=[], coverage=[]),
            ProjectHistoryBatch(project_id=project_id, fingerprint='fixture', evidence=[]),
            ProjectKnowledge(project_id=project_id, snapshot={}),
            ProjectScheme(project_id=project_id, snapshot={}),
            ProjectQA(project_id=project_id, generated_at='fixture', items=[], sources=[]),
            GenerationRevision(project_id=project_id, kind='brief', generated_at='fixture', snapshot={}),
            ProjectAIInstructions(project_id=project_id, instructions='Fixture'),
            ProjectGenerationAttempt(project_id=project_id, kind='brief',
                                     started_at='fixture', state='completed'),
        ])
        db.execute(text('INSERT INTO codex_history_index (project_id,indexed_at,indexed_turns) '
                        "VALUES (:pid,'fixture',1)"), {'pid': project_id})
        db.execute(text('INSERT INTO codex_history_turns '
                        '(project_id,session_id,session_title,turn_id,status,messages,content) '
                        "VALUES (:pid,:sid,'Fixture','saved-turn','completed','[]',:content)"),
                   {'pid': project_id, 'sid': session.id, 'content': search_term})
        db.commit()


def test_project_removal_clears_owned_data_and_keeps_external_sources(client, tmp_path):
    repository = tmp_path / 'repository'
    subprocess.run(['git', 'init', '--quiet', str(repository)], check=True)
    source = repository / 'source.txt'
    source.write_text('External repository source')
    thread_id = 'a57d3b2e-99b6-47da-8dcd-d7823515292f'
    history = tmp_path / 'codex' / 'sessions' / f'rollout-{thread_id}.jsonl'
    history.parent.mkdir(parents=True)
    history.write_text('{"type":"session_meta"}\n')

    kept = post(client, '/api/projects', {'name': 'Keep'})['id']
    removed = post(client, '/api/projects', {'name': 'Remove', 'repository_path': str(repository)})['id']
    kept_path = f'/api/projects/{kept}'
    removed_path = f'/api/projects/{removed}'
    engine = client.app.state.engine
    for path, project_id, term, thread in ((kept_path, kept, 'retainedneedle', 'other-thread'),
                                           (removed_path, removed, 'removalneedle', thread_id)):
        question_id = post(client, path + '/questions', {'title': 'Which approach?'})['id']
        question_path = f'{path}/questions/{question_id}'
        for solution in ('First method', 'Revised method'):
            answer_id = post(client, question_path + '/answers', {'solution': solution})['id']
            accepted = post(client, question_path + '/accept',
                            {'answer_id': answer_id, 'create_task': True}, expected_status=200)
        post(client, path + '/notes', {'title': term})
        seed_codex_records(engine, project_id, thread, accepted['decision']['id'], term)
        assert client.get(path + '/search', params={'q': term}).json()
        assert len(client.get(path + '/codex/history/search', params={'q': term}).json()['results']) == 1

    result = client.delete(removed_path)
    assert result.status_code == 204 and result.content == b'', result.text
    assert client.delete(removed_path).status_code == 404
    assert client.get(removed_path).status_code == 404
    assert [project['id'] for project in client.get('/api/projects').json()] == [kept]
    assert [project['id'] for project in client.get('/api/projects?compact=true').json()] == [kept]
    assert [decision['status'] for decision in client.get(kept_path + '/decisions').json()] == [
        'superseded', 'accepted',
    ]
    assert client.get(kept_path + '/search', params={'q': 'retainedneedle'}).json()
    assert len(client.get(kept_path + '/codex/history/search', params={
        'q': 'retainedneedle',
    }).json()['results']) == 1
    assert source.read_text() == 'External repository source'
    assert (repository / '.git' / 'HEAD').is_file()
    assert history.read_text() == '{"type":"session_meta"}\n'

    with engine.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA foreign_key_check').all() == []
        for table in (
            'questions', 'answers', 'decisions', 'tasks', 'notes', 'audit_events',
            'codex_sessions', 'context_packages', 'deliveries', 'codex_events',
            'codex_history_index', 'codex_history_turns', 'project_briefs',
            'project_history_batches', 'project_knowledge', 'project_schemes',
            'project_qa', 'generation_revisions', 'project_ai_instructions',
            'project_generation_attempts', 'search_index',
        ):
            ids = connection.execute(text(f'SELECT DISTINCT project_id FROM {table}')).scalars().all()
            assert ids == [kept], table
        assert connection.execute(text("SELECT rowid FROM codex_history_fts "
                                       "WHERE codex_history_fts MATCH 'removalneedle'")).first() is None

    replacement = post(client, '/api/projects', {'name': 'New project'})['id']
    assert replacement > removed
    assert client.get(removed_path).status_code == 404


def test_project_removal_rolls_back_if_database_rejects_it(client):
    project_id = post(client, '/api/projects', {'name': 'Keep on failure'})['id']
    path = f'/api/projects/{project_id}'
    note = post(client, path + '/notes', {'title': 'Rollbackneedle'})
    with client.app.state.engine.begin() as connection:
        connection.exec_driver_sql("CREATE TRIGGER reject_project_removal BEFORE DELETE ON projects "
                                   "BEGIN SELECT RAISE(ABORT, 'Fixture deletion failure'); END")

    assert client.delete(path).status_code == 409
    assert client.get(path).status_code == 200
    assert client.get(path + '/notes').json() == [note]
    assert client.get(path + '/search', params={'q': 'Rollbackneedle'}).json()


def test_project_removal_is_blocked_until_an_existing_mutation_finishes(client, monkeypatch):
    project_id = post(client, '/api/projects', {'name': 'Editing'})['id']
    path = f'/api/projects/{project_id}'
    entered, resume = Event(), Event()
    validate_repository = main.validate_repository

    def pause_validation(value):
        entered.set()
        assert resume.wait(5), 'project update was not released'
        return validate_repository(value)

    monkeypatch.setattr(main, 'validate_repository', pause_validation)
    with ThreadPoolExecutor(max_workers=1) as pool:
        updating = pool.submit(client.patch, path, json={'repository_path': None})
        try:
            assert entered.wait(5), 'project update did not enter validation'
            assert client.delete(path).status_code == 409
        finally:
            resume.set()
        assert updating.result(timeout=5).status_code == 200
    assert client.delete(path).status_code == 204


def test_project_removal_cleans_only_its_imported_document_copies(client):
    removed = post(client, '/api/projects', {'name': 'Remove'})['id']
    kept = post(client, '/api/projects', {'name': 'Keep'})['id']
    removed_path = f'/api/projects/{removed}'
    kept_path = f'/api/projects/{kept}'
    uploads = client.app.state.data_dir / 'uploads'

    def upload(path, filename):
        result = client.post(path + '/documents', files={'file': (filename, b'document', 'text/plain')})
        assert result.status_code == 201, result.text
        return result.json()

    old_note = upload(removed_path, 'old.txt')
    new_note = upload(removed_path, 'new.md')
    kept_note = upload(kept_path, 'other.txt')
    assert client.delete(f"{removed_path}/notes/{old_note['id']}").status_code == 204
    assert (uploads / old_note['reference']).exists()
    post(client, removed_path + '/notes', {
        'title': 'Forged upload reference', 'kind': 'document', 'reference': kept_note['reference'],
    })

    assert client.delete(removed_path).status_code == 204
    assert not (uploads / old_note['reference']).exists()
    assert not (uploads / new_note['reference']).exists()
    assert (uploads / kept_note['reference']).read_bytes() == b'document'


def test_removed_upload_cleanup_is_retried_when_the_app_restarts(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    data_dir = tmp_path / 'data'
    uploads = data_dir / 'uploads'
    app_copies = data_dir / 'saved-uploads'
    external = tmp_path / 'external'
    external.mkdir()
    with TestClient(create_app(data_dir)) as client:
        project_id = post(client, '/api/projects', {'name': 'Remove'})['id']
        path = f'/api/projects/{project_id}'
        imported = client.post(path + '/documents', files={'file': ('copy.txt', b'copy', 'text/plain')})
        assert imported.status_code == 201, imported.text
        filename = imported.json()['reference']
        (external / filename).write_bytes(b'external file')
        uploads.rename(app_copies)
        uploads.symlink_to(external, target_is_directory=True)

        assert client.delete(path).status_code == 204
        assert (app_copies / filename).is_file()
        assert (external / filename).read_bytes() == b'external file'

    uploads.unlink()
    app_copies.rename(uploads)
    with TestClient(create_app(data_dir)) as restarted:
        assert restarted.get(path).status_code == 404
        assert not (uploads / filename).exists()
        assert (external / filename).read_bytes() == b'external file'


@pytest.mark.parametrize('active', ['session', 'generation'])
def test_project_removal_waits_for_active_work(client, active):
    project_id = post(client, '/api/projects', {'name': 'Busy'})['id']
    path = f'/api/projects/{project_id}'
    with Session(client.app.state.engine) as db:
        if active == 'session':
            row = CodexSession(project_id=project_id, thread_id='busy-session', status='running')
        else:
            row = ProjectGenerationAttempt(project_id=project_id, kind='scheme',
                                           started_at='fixture', state='running')
        db.add(row)
        db.commit()
        result = client.delete(path)
        assert result.status_code == 409, result.text
        assert client.get(path).status_code == 200
        if active == 'session':
            row.status = 'idle'
        else:
            row.state = 'completed'
        db.commit()

    assert client.delete(path).status_code == 204
