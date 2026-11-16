"""Actual manager with isolated protocol fixtures; no Codex/OpenAI execution."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from types import SimpleNamespace
import threading

import pytest
from sqlalchemy import create_engine

from backend.app.models import Base
from backend.app.integrations.codex import manager as module
from backend.app.integrations.codex.protocol import CodexError


class GuardedServer:
    def __init__(self, *, state, disabled_mcp=(), on_event=None, on_disconnect=None):
        self.state = state
        self.disabled_mcp = frozenset(disabled_mcp)
        self.on_event = on_event or (lambda *args: None)
        self.on_disconnect = on_disconnect or (lambda: None)
        self.closed = False
        self.calls = []
        self.thread_count = 0
        state.clients.append(self)

    def request(self, method, params):
        self.calls.append((method, params))
        if method == 'account/read':
            return {'account': {'type': 'chatgpt'}}
        if method == 'config/read':
            return getattr(self.state, 'config_reply', {'config': {'mcp_servers': self.state.servers}})
        if method == 'thread/start':
            self.thread_count += 1
            return {'thread': {'id': f'private-{id(self)}-{self.thread_count}', 'ephemeral': True}}
        if method == 'mcpServerStatus/list':
            if hasattr(self.state, 'inventory_reply'):
                return self.state.inventory_reply
            names = list(self.state.servers) if isinstance(self.state.servers, dict) else []
            if self.state.inject_connector:
                names.append('unexpected')
            return {'data': [{
                'name': name, 'tools': {} if name in self.disabled_mcp and not self.state.force_enabled else {'external_write': {}},
                'resources': [], 'resourceTemplates': [], 'serverCapabilities': None,
            } for name in names], 'nextCursor': None}
        if method == 'turn/start':
            # A fake that would accept model work proves the manager's guard
            # precedes the request, rather than relying on a model instruction.
            if self.state.barrier:
                self.state.barrier.wait(timeout=3)
            self.on_event('item/completed', {
                'threadId': params['threadId'], 'turnId': 'turn',
                'item': {'type': 'agentMessage', 'phase': 'final_answer', 'text': '{"ok":true}'},
            })
            self.on_event('turn/completed', {
                'threadId': params['threadId'], 'turn': {'id': 'turn', 'status': 'completed'},
            })
            return {'turn': {'id': 'turn'}}
        if method == 'thread/unsubscribe':
            return {'status': 'unsubscribed'}
        return {}

    def close(self):
        self.closed = True


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'guard.sqlite3'))
    Base.metadata.create_all(engine)
    monkeypatch.setattr(module, 'availability', lambda *args: {
        'installed': True, 'app_server': True, 'authenticated': True, 'error': '',
    })
    state = SimpleNamespace(clients=[], servers={'inherited-cloud': {}},
                            force_enabled=False, inject_connector=False, barrier=None)
    manager = module.CodexManager(engine, tmp_path,
        client_factory=lambda **kwargs: GuardedServer(state=state, **kwargs))
    yield manager, state
    manager.close()
    engine.dispose()


@pytest.mark.parametrize('flag', [None, False, True])
def test_all_call_modes_disable_and_verify_inherited_servers_before_model_work(guarded, flag):
    manager, state = guarded
    kwargs = {} if flag is None else {'verify_connectors': flag}
    assert manager.generate_brief('Untrusted project evidence', {}, **kwargs) == '{"ok":true}'
    bootstrap, isolated = state.clients
    assert bootstrap.closed and isolated.closed
    assert [method for method, _ in bootstrap.calls] == ['account/read', 'config/read']
    assert isolated.disabled_mcp == {'inherited-cloud'}
    methods = [method for method, _ in isolated.calls]
    assert methods == ['account/read', 'thread/start', 'mcpServerStatus/list', 'turn/start']
    start = next(params for method, params in isolated.calls if method == 'thread/start')
    turn = next(params for method, params in isolated.calls if method == 'turn/start')
    assert start['config']['web_search'] == 'disabled'
    assert start['approvalPolicy'] == turn['approvalPolicy'] == 'never'
    assert turn['sandboxPolicy'] == {'type': 'readOnly', 'networkAccess': False}


@pytest.mark.parametrize('failure', ['enabled', 'unexpected', 'bad-config'])
def test_uncertain_isolation_fails_before_any_model_request(guarded, failure):
    manager, state = guarded
    state.force_enabled = failure == 'enabled'
    state.inject_connector = failure == 'unexpected'
    if failure == 'bad-config':
        state.servers = []
    with pytest.raises(CodexError, match='MCP'):
        manager.generate_brief('Do not deliver', {})
    assert all(client.closed for client in state.clients)
    assert not any(method == 'turn/start' for client in state.clients for method, _ in client.calls)


@pytest.mark.parametrize(('field', 'reply'), [
    ('config_reply', None), ('config_reply', []),
    ('inventory_reply', None), ('inventory_reply', []),
])
def test_malformed_guard_responses_fail_closed_with_explicit_error(guarded, field, reply):
    manager, state = guarded
    setattr(state, field, reply)
    with pytest.raises(CodexError, match='MCP'):
        manager.generate_brief('Do not deliver', {})
    assert all(client.closed for client in state.clients)
    assert not any(method == 'turn/start' for client in state.clients for method, _ in client.calls)


def test_pool_discovers_once_and_verifies_each_fresh_thread_without_new_servers(guarded):
    manager, state = guarded
    with manager.generation_session():
        assert manager.generate_brief('first', {}) == '{"ok":true}'
        isolated = state.clients[-1]
        assert not isolated.closed
        assert manager.generate_brief('second', {}, verify_connectors=True) == '{"ok":true}'
        assert len(state.clients) == 2
        methods = [method for method, _ in isolated.calls]
        assert methods.count('mcpServerStatus/list') == 2
        assert methods.count('thread/unsubscribe') == 2
        assert sum(method == 'config/read' for client in state.clients for method, _ in client.calls) == 1
    assert isolated.closed


def test_reused_pool_rejects_a_connector_appearing_after_first_thread(guarded):
    manager, state = guarded
    with manager.generation_session():
        assert manager.generate_brief('first', {}) == '{"ok":true}'
        state.inject_connector = True
        with pytest.raises(CodexError, match='MCP'):
            manager.generate_brief('must not deliver', {})
        assert state.clients[-1].closed
        with pytest.raises(CodexError, match='transport failed'):
            manager.generate_brief('no implicit retry', {})
    assert sum(method == 'turn/start' for client in state.clients for method, _ in client.calls) == 1


def test_parallel_workers_keep_isolation_and_transport_ownership_independent(guarded):
    manager, state = guarded
    state.barrier = threading.Barrier(2)
    def run():
        with manager.independent_generation() as worker:
            return worker.generate_brief('worker', {})
    contexts = [copy_context(), copy_context()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(context.run, run) for context in contexts]
        assert [future.result(timeout=5) for future in futures] == ['{"ok":true}'] * 2
    assert len(state.clients) == 4
    assert all(client.closed for client in state.clients)
    for client in state.clients:
        methods = [method for method, _ in client.calls]
        if 'turn/start' in methods:
            assert client.disabled_mcp == {'inherited-cloud'}
            assert methods.index('mcpServerStatus/list') < methods.index('turn/start')
