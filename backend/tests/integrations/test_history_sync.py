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


def test_original_account_reader_is_used(env, tmp_path, monkeypatch):
    home = tmp_path / 'account'; home.mkdir()
    with Session(env.engine) as db:
        db.get(CodexSession, 1).codex_account = 'profile:one'; db.commit()
    monkeypatch.setattr(module, 'account_home', lambda key: str(home) if key == 'profile:one' else None)
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
