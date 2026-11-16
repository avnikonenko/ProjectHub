"""Real SQLite/HTTP regression cases for release path and transaction safety."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import threading

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.main import create_app
from backend.app.models import AuditEvent, Note, Project, Task


@pytest.mark.parametrize('name', ['chosen?folder', 'chosen#folder', 'chosen%folder', 'space and ?#%'])
def test_data_directory_is_a_literal_path_and_sibling_files_are_untouched(tmp_path, name):
    sibling = tmp_path / name.split('?')[0]
    # The former URL concatenation parsed ? as a query and migrated this file.
    if '?' in name:
        with sqlite3.connect(sibling) as db:
            db.execute('CREATE TABLE sentinel(value TEXT)')
            db.execute("INSERT INTO sentinel VALUES ('unchanged')")
        original = sibling.read_bytes()
    target = tmp_path / name
    engine, directory = initialize(target)
    try:
        assert directory == target.absolute()
        assert engine.url.database == str(target.absolute() / 'projecthub.sqlite3')
        assert (target / 'projecthub.sqlite3').is_file()
        with Session(engine) as db:
            db.add(Project(name='Saved here'))
            db.commit()
        if '?' in name:
            assert sibling.read_bytes() == original
    finally:
        engine.dispose()


def test_http_restart_and_backup_use_the_literal_data_path(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    target = tmp_path / 'data?with#percent%'
    app = create_app(target)
    with TestClient(app) as client:
        created = client.post('/api/projects', json={'name': 'Persist me'}).json()
        saved_backup = client.post('/api/backup').json()
        assert Path(saved_backup['filename']).name == saved_backup['filename']
        assert (target / 'backups' / saved_backup['filename']).is_file()
    app.state.engine.dispose()
    restarted = create_app(target)
    with TestClient(restarted) as client:
        assert client.get(f"/api/projects/{created['id']}").json()['name'] == 'Persist me'
    restarted.state.engine.dispose()


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    app = create_app(tmp_path / 'data')
    with TestClient(app) as client:
        one = client.post('/api/projects', json={'name': 'One'}).json()['id']
        two = client.post('/api/projects', json={'name': 'Two'}).json()['id']
        yield app, client, one, two
    app.state.engine.dispose()


URL = 'https://chatgpt.com/share/11111111-1111-1111-1111-111111111111'


def test_concurrent_import_rechecks_uniqueness_after_fetch_without_holding_db_connection(state, monkeypatch):
    from backend.app.integrations import chatgpt_share
    app, client, pid, _ = state
    entered = threading.Barrier(3)
    release = threading.Event()

    def fetch(url):
        assert url == URL
        entered.wait(timeout=5)
        assert release.wait(5)
        return 'Imported', 'User:\nOriginal source'

    monkeypatch.setattr(chatgpt_share, 'read_transcript', fetch)
    path = f'/api/projects/{pid}/notes/chatgpt-share'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, path, json={'url': url}) for url in (URL, URL + '?utm_source=example')]
        try:
            entered.wait(timeout=5)
            assert app.state.engine.pool.checkedout() == 0
            assert client.post(f'/api/projects/{pid}/notes', json={'title': 'Unrelated write'}).status_code == 201
        finally:
            release.set()
        replies = [future.result(timeout=5) for future in futures]
    assert sorted(response.status_code for response in replies) == [201, 409]
    with Session(app.state.engine) as db:
        rows = list(db.scalars(select(Note).where(Note.project_id == pid, Note.reference == URL)))
        assert len(rows) == 1
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.project_id == pid,
                         AuditEvent.entity_type == 'notes', AuditEvent.entity_id == rows[0].id)))
        assert len(events) == 1


def test_same_share_can_be_imported_into_separate_projects(state, monkeypatch):
    from backend.app.integrations import chatgpt_share
    _, client, one, two = state
    ready = threading.Barrier(2)
    def fetch(url):
        ready.wait(timeout=5)
        return 'Shared', 'User:\nOriginal source'
    monkeypatch.setattr(chatgpt_share, 'read_transcript', fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, f'/api/projects/{pid}/notes/chatgpt-share', json={'url': URL}) for pid in (one, two)]
        replies = [future.result(timeout=5) for future in futures]
    assert [response.status_code for response in replies] == [201, 201]
    assert [response.json()['project_id'] for response in replies] == [one, two]


def test_preexisting_duplicate_imports_are_preserved_and_not_refetched(state, monkeypatch):
    from backend.app.integrations import chatgpt_share
    app, client, pid, _ = state
    with Session(app.state.engine) as db:
        db.add_all([Note(project_id=pid, title=f'Existing {i}', content='Source', kind='document', reference=URL) for i in range(2)])
        db.commit()
    monkeypatch.setattr(chatgpt_share, 'read_transcript', lambda _: pytest.fail('Existing import was refetched'))
    assert client.post(f'/api/projects/{pid}/notes/chatgpt-share', json={'url': URL}).status_code == 409
    with Session(app.state.engine) as db:
        assert len(list(db.scalars(select(Note).where(Note.project_id == pid, Note.reference == URL)))) == 2


def test_decision_task_creation_and_question_reopening_are_serialized(state, monkeypatch):
    from backend.app import main
    app, client, pid, _ = state
    base = f'/api/projects/{pid}'
    question = client.post(base + '/questions', json={'title': 'Choose method'}).json()
    aid = client.post(base + f"/questions/{question['id']}/answers", json={'solution': 'Accepted method'}).json()['id']
    accepted = client.post(base + f"/questions/{question['id']}/accept", json={'answer_id': aid}).json()
    did = accepted['decision']['id']
    task_entered, release_task, reopen_attempted, reopen_finished = [threading.Event() for _ in range(4)]
    original = main.make_task

    def paused_task(*args, **kwargs):
        task_entered.set()
        assert release_task.wait(5)
        return original(*args, **kwargs)

    def before_sql(connection, cursor, statement, parameters, context, many):
        if statement == 'BEGIN IMMEDIATE' and task_entered.is_set():
            reopen_attempted.set()

    monkeypatch.setattr(main, 'make_task', paused_task)
    event.listen(app.state.engine, 'before_cursor_execute', before_sql)
    def reopen():
        try:
            return client.post(base + f"/questions/{question['id']}/reopen")
        finally:
            reopen_finished.set()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            created = pool.submit(client.post, base + f'/decisions/{did}/tasks', json={})
            assert task_entered.wait(5)
            reopened = pool.submit(reopen)
            try:
                assert reopen_attempted.wait(5)
                assert not reopen_finished.wait(.1)
            finally:
                release_task.set()
            assert created.result(timeout=5).status_code == 201
            assert reopened.result(timeout=5).status_code == 200
    finally:
        event.remove(app.state.engine, 'before_cursor_execute', before_sql)
        release_task.set()
    assert client.post(base + f'/decisions/{did}/tasks', json={}).status_code == 409
    with Session(app.state.engine) as db:
        tasks = list(db.scalars(select(Task).where(Task.project_id == pid)))
        assert len(tasks) == 1
        actions = list(db.scalars(select(AuditEvent.action).where(AuditEvent.project_id == pid).order_by(AuditEvent.id)))
        assert actions.index('created_from_decision') < actions.index('reopened')
