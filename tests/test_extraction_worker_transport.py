"""Worker transport tests use the real manager with a fake App Server protocol."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from functools import partial
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from backend.app.models import Base
from backend.app.integrations.codex import manager as module, progress
from backend.app.integrations.codex.ai_instructions import use_project_instructions
from backend.app.integrations.codex.protocol import CodexError


@pytest.fixture
def manager(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'workers.sqlite3'))
    Base.metadata.create_all(engine)
    monkeypatch.setattr(module, 'availability', lambda *args: {
        'installed': True, 'app_server': True, 'authenticated': True, 'error': '',
    })
    value = module.CodexManager(engine, tmp_path)
    yield value
    value.close()
    engine.dispose()


class ProtocolClient:
    def __init__(self, *, on_event, on_disconnect, barrier=None, abort=None):
        self.on_event, self.on_disconnect = on_event, on_disconnect
        self.barrier, self.abort = barrier, abort
        self.calls = []
        self.closed = False
        self.number = 0

    def request(self, method, params):
        self.calls.append((method, params))
        if method == 'account/read':
            return {'account': {'type': 'chatgpt'}}
        if method == 'config/read':
            return {'config': {'mcp_servers': {}}}
        if method == 'mcpServerStatus/list':
            return {'data': [], 'nextCursor': None}
        if method == 'thread/start':
            self.number += 1
            return {'thread': {'id': f'thread-{id(self)}-{self.number}', 'ephemeral': True}}
        if method == 'thread/unsubscribe':
            return {'status': 'unsubscribed'}
        if method == 'turn/start':
            if self.abort is not None:
                self.abort.set()
                return {'turn': {'id': 'private-turn'}}
            if self.barrier is not None:
                self.barrier.wait(timeout=2)
            self.on_event('item/completed', {
                'threadId': params['threadId'], 'turnId': 'private-turn',
                'item': {'type': 'agentMessage', 'phase': 'final_answer', 'text': '{"ok":true}'},
            })
            self.on_event('turn/completed', {
                'threadId': params['threadId'], 'turn': {'id': 'private-turn', 'status': 'completed'},
            })
            return {'turn': {'id': 'private-turn'}}
        return {}

    def close(self):
        self.closed = True


def test_workers_rebind_selected_model_and_keep_transports_and_metadata_private(manager, monkeypatch):
    clients = []
    barrier = threading.Barrier(2)

    def factory(**callbacks):
        client = ProtocolClient(**callbacks, barrier=barrier if len(clients) else None)
        clients.append(client)
        return client

    manager.client_factory = factory
    manager.account_home, manager.account_key = '/account/home', 'chosen-account'
    monkeypatch.setattr(manager, 'validate_generation', lambda *args: None)
    selected = manager.for_generation('chosen-model', 'medium')
    # A worker must not call discovery or a constructor again.
    monkeypatch.setattr(module.CodexManager, '__init__', lambda *args, **kwargs: pytest.fail('worker constructed a manager'))
    monkeypatch.setattr(selected, 'validate_generation', lambda *args: pytest.fail('worker rediscovered models'))

    def run():
        with selected.independent_generation() as worker:
            assert worker is not selected
            assert worker.account_home == selected.account_home
            assert worker.client_factory is selected.client_factory
            assert isinstance(worker.generate_brief, partial)
            assert worker.generate_brief.args[0] is worker
            worker.generation_metadata.setdefault('private', []).append('worker')
            assert worker.generate_brief('worker', {}) == '{"ok":true}'
            return deepcopy(worker.generation_metadata)

    with use_project_instructions('Keep this project-specific rule.'), selected.generation_session():
        assert selected.generate_brief('parent', {}) == '{"ok":true}'
        before = deepcopy(selected.generation_metadata)
        contexts = [copy_context(), copy_context()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(context.run, run) for context in contexts]
            metadata = [future.result(timeout=3) for future in futures]
        assert selected.generation_metadata == before
        assert not clients[0].closed
        assert all(client.closed for client in clients[1:])
        assert metadata == [before, before]
    assert len(clients) == 3 and all(client.closed for client in clients)
    for client in clients:
        starts = [params for method, params in client.calls if method == 'thread/start']
        assert all(params['model'] == 'chosen-model' and params['approvalPolicy'] == 'never' for params in starts)
        assert all('Keep this project-specific rule.' in params['developerInstructions'] for params in starts)
        turns = [params for method, params in client.calls if method == 'turn/start']
        assert all(params['effort'] == 'medium' and params['sandboxPolicy'] == {
            'type': 'readOnly', 'networkAccess': False} for params in turns)


def test_sibling_abort_wakes_generation_wait_without_active_project():
    abort, waiting, done = threading.Event(), threading.Event(), threading.Event()

    def stop():
        assert waiting.wait(2)
        abort.set()

    thread = threading.Thread(target=stop)
    thread.start()
    started = time.monotonic()

    def wait(seconds):
        waiting.set()
        return done.wait(seconds)

    try:
        with progress.abort_on(abort), pytest.raises(CodexError, match='Another extraction batch failed'):
            module.wait_for_generation(SimpleNamespace(wait=wait), 600)
        assert time.monotonic() - started < 1
        assert progress.current_abort_event() is None
    finally:
        thread.join(timeout=2)


def test_sibling_abort_interrupts_own_turn_without_marking_project_cancelled(manager):
    project_id = 97831
    abort, clients = threading.Event(), []

    def factory(**callbacks):
        client = ProtocolClient(**callbacks, abort=abort)
        clients.append(client)
        return client

    manager.client_factory = factory
    progress.begin(project_id, 'knowledge')
    try:
        with progress.abort_on(abort), manager.independent_generation() as worker:
            with pytest.raises(CodexError, match='Another extraction batch failed') as error:
                worker.generate_brief('worker', {})
        assert progress.current_cancel_event().is_set() is False
        assert progress.read(project_id, 'knowledge')['state'] == 'running'
    finally:
        progress.finish(project_id, error='The original extraction error')
    assert not isinstance(error.value, progress.GenerationCancelled)
    assert progress.read(project_id, 'knowledge')['state'] == 'failed'
    assert all(client.closed for client in clients)
    interrupts = [params for method, params in clients[0].calls if method == 'turn/interrupt']
    assert interrupts == [{'threadId': f'thread-{id(clients[0])}-1', 'turnId': 'private-turn'}]
    assert not any(method == 'thread/resume' for method, _ in clients[0].calls)


def test_real_cancellation_and_budget_take_precedence_over_sibling_abort(monkeypatch):
    project_id = 97832
    abort = threading.Event()
    abort.set()
    progress.begin(project_id, 'knowledge')
    try:
        started = progress.read(project_id, 'knowledge')['started_at']
        progress.cancel(project_id, 'knowledge', started)
        with progress.abort_on(abort), pytest.raises(progress.GenerationCancelled):
            progress.check_cancelled()
    finally:
        progress.finish(project_id)
    assert progress.read(project_id, 'knowledge')['state'] == 'cancelled'

    clock = [100.0]
    monkeypatch.setenv('PROJECTHUB_GENERATION_BUDGET', '30')
    monkeypatch.setattr(progress.time, 'monotonic', lambda: clock[0])
    progress.begin(project_id, 'knowledge')
    try:
        clock[0] += 31
        with progress.abort_on(abort), pytest.raises(CodexError, match='overall time budget'):
            progress.check_cancelled()
    finally:
        progress.finish(project_id, error='Budget exhausted')
