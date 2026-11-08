"""Real SQLite indexing with fake read-only App Server metadata/history."""
from types import SimpleNamespace
from threading import Event, Thread

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import CodexSession, Project
from backend.app.integrations.codex import history_sync as module
from backend.app.integrations.codex.history_search import search_history
from backend.app.integrations.codex.protocol import CodexError


class FakeClient:
    def __init__(self, state, calls, home='launch'):
        self.state, self.calls, self.home = state, calls, home
        self.closed = False

    def request(self, method, params):
        assert not self.closed
        self.calls.append((self.home, method, params))
        row = self.state[params['threadId']]
        if row.get('fail'):
            raise CodexError('private arbitrary server detail')
        if method == 'thread/read':
            return {'thread': {'id': params['threadId'], 'updatedAt': row.get('updated', 1),
                               'status': {'type': row.get('status', 'notLoaded')}}}
        assert method == 'thread/turns/list'
        assert params['itemsView'] == 'full' and params['sortDirection'] == 'asc'
        return {'data': [{'id': 'turn', 'status': 'completed', 'items': [
            {'type': 'userMessage', 'content': [{'type': 'text', 'text': 'question'}]},
            {'type': 'agentMessage', 'text': row['text']},
        ]}], 'nextCursor': None}

    def close(self):
        self.closed = True


@pytest.fixture
def env(tmp_path):
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')]); db.flush()
        db.add_all([CodexSession(id=1, project_id=1, thread_id='a', status='linked'),
                    CodexSession(id=2, project_id=1, thread_id='b', status='idle'),
                    CodexSession(id=3, project_id=2, thread_id='c', status='linked')]); db.commit()
    state = {'a': {'text': 'alpha'}, 'b': {'text': 'bravo'}, 'c': {'text': 'charlie'}}
    calls, clients = [], []

    def create():
        client = FakeClient(state, calls)
        clients.append(client)
        return client

    worker = module.HistorySync(engine, lambda: SimpleNamespace(_new_read_client=create))
    yield SimpleNamespace(engine=engine, worker=worker, state=state, calls=calls, clients=clients)
    worker.close()
    engine.dispose()


def search(env, project, query):
    with Session(env.engine) as db:
        return search_history(db, project, query)['results']


def test_sync_updates_only_changed_sessions_without_model_work(env):
    env.worker.sync_once()
    assert search(env, 1, 'alpha') and search(env, 1, 'bravo')
    assert not search(env, 2, 'alpha')
    count = sum(method == 'thread/turns/list' for _, method, _ in env.calls)
    env.worker.sync_once()
    assert sum(method == 'thread/turns/list' for _, method, _ in env.calls) == count
    env.state['a'].update(updated=2, text='replacement')
    env.worker.sync_once()
    assert not search(env, 1, 'alpha') and search(env, 1, 'replacement')
    assert sum(method == 'thread/turns/list' for _, method, _ in env.calls) == count + 1
    assert {method for _, method, _ in env.calls} == {'thread/read', 'thread/turns/list'}
    assert all(client.closed for client in env.clients)
    assert env.worker.read_status(1) == {'error': None}


def test_one_failure_preserves_old_index_and_other_sessions_sync(env):
    env.worker.sync_once()
    env.state['a'].update(fail=True, updated=2)
    env.state['b'].update(text='fresh', updated=2)
    env.worker.sync_once()
    assert search(env, 1, 'alpha') and search(env, 1, 'fresh')
    warning = env.worker.read_status(1)['error']
    assert '#1' in warning and 'private' not in warning
    assert env.worker.read_status(2) == {'error': None}
    env.state['a'].update(fail=False)
    env.worker.sync_once()
    assert env.worker.read_status(1) == {'error': None}


def test_active_threads_wait_and_missing_timestamps_reconcile(env):
    env.state['a']['status'] = 'active'
    env.state['b']['updated'] = None
    env.worker.sync_once()
    assert not search(env, 1, 'alpha')
    env.state['a']['status'] = 'notLoaded'
    env.state['b']['text'] = 'fresh'
    env.worker.sync_once()
    assert search(env, 1, 'alpha') and search(env, 1, 'fresh')


def test_codex_activity_uses_existing_metadata_without_touching_local_timestamps(env):
    with Session(env.engine) as db:
        original = db.get(CodexSession, 1).updated_at
    env.state['a'].update(status='active', updated=2)
    env.state['b']['updated'] = 10 ** 400
    env.worker.sync_once()
    assert not search(env, 1, 'alpha')  # Active threads still wait for indexing.
    with Session(env.engine) as db:
        session = db.get(CodexSession, 1)
        assert session.codex_activity_at == '1970-01-01T00:00:02+00:00'
        assert session.updated_at == original
        assert db.get(CodexSession, 2).codex_activity_at is None
    assert [method for _, method, params in env.calls if params['threadId'] == 'a'] == ['thread/read']
    env.state['a']['updated'] = None
    env.worker.sync_once()
    with Session(env.engine) as db:
        assert db.get(CodexSession, 1).codex_activity_at == '1970-01-01T00:00:02+00:00'


@pytest.mark.parametrize('registered', [False, True])
def test_original_account_reader_is_used(env, tmp_path, monkeypatch, registered):
    home = tmp_path / 'account'; home.mkdir()
    from backend.app.models import CodexProfile
    from backend.app.integrations.codex import accounts
    with Session(env.engine) as db:
        if registered:
            db.add(CodexProfile(id=1, name='Registered', directory=str(home)))
        db.get(CodexSession, 1).codex_account = 'registered:1' if registered else 'profile:one'
        db.commit()
    monkeypatch.setattr(accounts, 'account_homes', lambda: {'profile:one': home})
    monkeypatch.setattr(module, 'AppServerClient', lambda codex_home: FakeClient(env.state, env.calls, codex_home))
    env.worker.sync_once()
    a_calls = [(origin, method) for origin, method, params in env.calls if params['threadId'] == 'a']
    assert a_calls == [(str(home), 'thread/read'), (str(home), 'thread/turns/list'), (str(home), 'thread/read')]


def test_closed_worker_does_not_read_or_write(env):
    env.worker.close()
    env.worker.sync_once()
    assert env.calls == []
    with env.engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM codex_history_turns')).scalar() == 0


def test_shutdown_interrupts_pending_read_and_waits_for_worker(env, monkeypatch):
    entered, released = Event(), Event()

    class BlockingClient(FakeClient):
        def request(self, method, params):
            entered.set()
            assert released.wait(3)
            raise CodexError('closed')

        def close(self):
            released.set()
            super().close()

    client = BlockingClient(env.state, env.calls)
    env.worker.manager_factory = lambda: SimpleNamespace(_new_read_client=lambda: client)
    # Exercise the same pass lock without waiting for the periodic timer.
    worker = Thread(target=env.worker.sync_once)
    worker.start()
    assert entered.wait(3)
    env.worker.close()
    worker.join(timeout=3)
    assert not worker.is_alive() and client.closed
    with env.engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM codex_history_turns')).scalar() == 0


def test_system_error_is_reported_without_touching_saved_history(env):
    env.worker.sync_once()
    env.state['a']['status'] = 'systemError'
    env.worker.sync_once()
    assert '#1' in env.worker.read_status(1)['error']
    assert search(env, 1, 'alpha')
    env.state['a']['status'] = 'notLoaded'
    env.worker.sync_once()
    assert env.worker.read_status(1) == {'error': None}


def test_metadata_change_during_index_requires_another_pass(env, monkeypatch):
    real_index = module.index_history
    changed = False

    def index_then_change(engine, project_id, reader, **options):
        nonlocal changed
        result = real_index(engine, project_id, reader, **options)
        if options['session_ids'] == {1} and not changed:
            env.state['a'].update(text='new version', updated=2)
            changed = True
        return result

    monkeypatch.setattr(module, 'index_history', index_then_change)
    env.worker.sync_once()
    assert search(env, 1, 'alpha')
    env.worker.sync_once()
    assert not search(env, 1, 'alpha') and search(env, 1, 'version')


def test_paused_session_is_persistent_and_keeps_history_without_warning(env):
    env.worker.sync_once()
    env.state['a']['fail'] = True
    env.worker.sync_once()
    assert '#1' in env.worker.read_status(1)['error']
    with Session(env.engine) as db:
        db.get(CodexSession, 1).auto_sync = False
        db.commit()
    # Status hides a stale in-memory warning immediately, before the next pass.
    assert env.worker.read_status(1) == {'error': None}
    env.calls.clear()
    env.worker.sync_once()
    assert all(params['threadId'] != 'a' for _, _, params in env.calls)
    assert search(env, 1, 'alpha')
    with Session(env.engine) as db:
        assert db.get(CodexSession, 1).auto_sync is False
        db.get(CodexSession, 1).auto_sync = True
        db.commit()
    env.worker.sync_once()
    assert '#1' in env.worker.read_status(1)['error']


def test_pause_during_failed_read_cannot_restore_warning(env, monkeypatch):
    original = FakeClient.request

    def request_then_pause(client, method, params):
        if params['threadId'] == 'a':
            with Session(env.engine) as db:
                db.get(CodexSession, 1).auto_sync = False
                db.commit()
            raise CodexError('unavailable')
        return original(client, method, params)

    monkeypatch.setattr(FakeClient, 'request', request_then_pause)
    env.worker.sync_once()
    assert env.worker.read_status(1) == {'error': None}
    assert (1, 1) not in env.worker._errors


def test_migration_enables_existing_sessions_without_changing_records(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    engine = create_engine('sqlite:///' + str(tmp_path / 'migration.sqlite3'))
    cfg = Config()
    cfg.set_main_option('script_location', 'migrations')
    with engine.begin() as connection:
        cfg.attributes['connection'] = connection
        command.upgrade(cfg, '0009')
        connection.exec_driver_sql(
            "INSERT INTO projects(id,name,description,goals,status,git_remote,tags,architecture_mermaid,created_at,updated_at) "
            "VALUES(1,'Example','','','planning','','[]','','now','now')")
        connection.exec_driver_sql(
            "INSERT INTO codex_sessions(id,project_id,codex_account,thread_id,title,status,last_error,created_at,updated_at) "
            "VALUES(1,1,NULL,'thread','Saved session','linked','','now','now')")
        command.upgrade(cfg, 'head')
    with Session(engine) as db:
        session = db.get(CodexSession, 1)
        assert session.auto_sync is True
        assert session.title == 'Saved session' and session.thread_id == 'thread'
        assert session.topic == '' and session.codex_activity_at is None
    engine.dispose()
