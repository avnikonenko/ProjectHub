"""Actual SQLite persistence with a fake App Server; no FastAPI dependency."""
from types import SimpleNamespace

import pytest
pytest.importorskip('sqlalchemy')
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from backend.app.models import Base, Project, Decision, CodexSession, Delivery, CodexEvent
from backend.app.integrations.codex import manager as module
from backend.app.integrations.codex.manager import CodexManager, context_preview, history_page
from backend.app.integrations.codex.protocol import CodexError


class FakeClient:
    account_type = 'chatgpt'
    def __init__(self, on_event=None, on_disconnect=None):
        self.on_event = on_event or (lambda *args: None)
        self.on_disconnect = on_disconnect or (lambda: None)
        self.calls = []
        self.complete_early = self.connectors = self.timeout = self.closed = False

    def request(self, method, params):
        self.calls.append((method, params))
        if method == 'account/read':
            return {'account': {'type': self.account_type}}
        if method in ('thread/start', 'thread/resume', 'thread/read'):
            return {'thread': {'id': params.get('threadId', 'thread-1'), 'status': {'type': 'idle'}}}
        if method == 'mcpServerStatus/list':
            return {'data': [{'name': 'inherited'}] if self.connectors else [], 'nextCursor': None}
        if method == 'turn/start':
            if self.timeout:
                raise CodexError('Request timed out; delivery may be unverified')
            self.on_event('turn/started', {'turn': {'id': 'turn-1', 'status': 'inProgress'}})
            if self.complete_early:
                self.on_event('turn/completed', {'turn': {'id': 'turn-1', 'status': 'completed'}})
            return {'turn': {'id': 'turn-1'}}
        return {}

    def close(self):
        self.closed = True


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'knowledge.sqlite3'))
    Base.metadata.create_all(engine)
    monkeypatch.setattr(module, 'availability', lambda: {
        'installed': True, 'app_server': True, 'authenticated': True, 'error': ''})
    FakeClient.account_type = 'chatgpt'
    with Session(engine) as db:
        project, other = Project(name='A'), Project(name='B')
        db.add_all([project, other]); db.commit(); db.refresh(project)
        db.add_all([Decision(project_id=1, title='Chosen', status='accepted'),
                    Decision(project_id=1, title='Hypothesis', status='proposed'),
                    Decision(project_id=2, title='Secret B', status='accepted')])
        db.commit(); db.refresh(project)
    manager = CodexManager(engine, tmp_path, client_factory=FakeClient)
    yield SimpleNamespace(engine=engine, project=project, manager=manager, path=tmp_path)
    manager.close(); engine.dispose()


def session_and_delivery(env):
    session = env.manager.open(env.project, 'Review')
    with Session(env.engine) as db:
        row = Delivery(project_id=env.project.id, session_id=session['id'], content='Reviewed prompt')
        db.add(row); db.commit(); db.refresh(row)
        return session['id'], row.id


def test_actual_sqlite_context_is_stable_and_scoped(env):
    with Session(env.engine) as db:
        a, b = context_preview(db, 1), context_preview(db, 1)
    assert a == b and len(a['version']) == 64
    assert 'Chosen' in a['content']
    assert 'Hypothesis' not in a['content'] and 'Secret B' not in a['content']


def test_actual_sqlite_send_read_only_and_no_duplicate(env):
    sid, did = session_and_delivery(env)
    assert env.manager.send(did)['status'] == 'sent'
    assert env.manager.send(did)['status'] == 'sent'
    client = env.manager.clients[sid]
    turns = [p for m, p in client.calls if m == 'turn/start']
    assert len(turns) == 1
    assert turns[0]['sandboxPolicy'] == {'type': 'readOnly', 'networkAccess': False}
    assert turns[0]['approvalPolicy'] == 'never'
    with Session(env.engine) as db:
        assert db.get(CodexSession, sid).status == 'running'


def test_actual_sqlite_completion_response_race(env):
    sid, did = session_and_delivery(env)
    env.manager.clients[sid].complete_early = True
    assert env.manager.send(did)['status'] == 'completed'
    with Session(env.engine) as db:
        assert db.get(CodexSession, sid).status == 'idle'
        assert db.get(Delivery, did).turn_id == 'turn-1'
        assert len(list(db.scalars(select(CodexEvent)))) == 2


def test_actual_sqlite_busy_and_disconnected_queue(env):
    sid, did = session_and_delivery(env)
    with Session(env.engine) as db:
        db.get(CodexSession, sid).status = 'running'; db.commit()
    assert env.manager.send(did)['status'] == 'queued'
    assert not any(m == 'turn/start' for m, _ in env.manager.clients[sid].calls)
    env.manager.clients.pop(sid).close()
    assert env.manager.send(did)['status'] == 'queued'


def test_actual_sqlite_ambiguous_timeout_preserved(env):
    sid, did = session_and_delivery(env)
    env.manager.clients[sid].timeout = True
    assert env.manager.send(did)['status'] == 'unverified'
    with pytest.raises(CodexError, match='already have reached'):
        env.manager.send(did)


def test_actual_sqlite_inherited_connector_fails_closed(env):
    sid, did = session_and_delivery(env)
    env.manager.clients[sid].connectors = True
    assert env.manager.send(did)['status'] == 'failed'
    assert not any(m == 'turn/start' for m, _ in env.manager.clients[sid].calls)


def test_actual_sqlite_restart_recovery(env):
    sid, did = session_and_delivery(env)
    with Session(env.engine) as db:
        db.get(CodexSession, sid).status = 'running'
        db.get(Delivery, did).status = 'sending'; db.commit()
    fresh = CodexManager(env.engine, env.path, client_factory=FakeClient)
    with Session(env.engine) as db:
        assert db.get(CodexSession, sid).status == 'interrupted'
        assert db.get(Delivery, did).status == 'unverified'
    assert fresh.clients == {}


def test_api_auth_refused_before_thread_creation(env):
    FakeClient.account_type = 'apiKey'
    with pytest.raises(CodexError, match='fallback is disabled'):
        env.manager.open(env.project, 'Discussion')
    assert env.manager.clients == {}


def test_auth_change_fails_closed_before_turn(env):
    sid, did = session_and_delivery(env)
    env.manager.clients[sid].account_type = 'apiKey'
    assert env.manager.send(did)['status'] == 'failed'
    assert not any(method == 'turn/start' for method, _ in env.manager.clients[sid].calls)


def test_external_active_thread_is_not_resumed(env):
    original = env.manager.client_factory
    clients = []
    def factory(**kwargs):
        client = original(**kwargs)
        request = client.request
        def wrapped(method, params):
            if method == 'thread/read':
                client.calls.append((method, params))
                return {'thread': {'id': params['threadId'], 'status': {'type': 'active'}}}
            return request(method, params)
        client.request = wrapped
        clients.append(client)
        return client
    env.manager.client_factory = factory
    with pytest.raises(CodexError, match='active elsewhere'):
        env.manager.open(env.project, 'Existing', 'external-thread')
    assert not any(method == 'thread/resume' for method, _ in clients[0].calls)
    assert clients[0].closed


def test_active_external_thread_can_be_linked_without_resume(env):
    clients = []
    original = env.manager.client_factory
    def factory(**kwargs):
        client = original(**kwargs)
        request = client.request
        def wrapped(method, params):
            if method == 'thread/read':
                client.calls.append((method, params))
                return {'thread': {'id': params['threadId'], 'status': {'type': 'active'}}}
            return request(method, params)
        client.request = wrapped
        clients.append(client)
        return client
    env.manager.client_factory = factory
    session = env.manager.associate(env.project, 'Existing', 'external-thread')
    assert session['status'] == 'linked'
    assert session['id'] not in env.manager.clients
    assert clients[0].calls == [('thread/read', {
        'threadId': 'external-thread', 'includeTurns': False})]
    assert clients[0].closed


def test_history_is_bounded_and_omits_tool_output():
    result = history_page({'data': [{
        'id': 'turn-1', 'status': 'completed', 'items': [
            {'type': 'userMessage', 'content': [{'type': 'text', 'text': 'Question'}]},
            {'type': 'commandExecution', 'command': 'private', 'output': 'secret'},
            {'type': 'agentMessage', 'text': 'Answer'},
        ],
    }], 'nextCursor': 'older'})
    assert result == {'turns': [{
        'id': 'turn-1', 'status': 'completed', 'messages': [
            {'role': 'user', 'text': 'Question'},
            {'role': 'assistant', 'text': 'Answer'},
        ],
    }], 'next_cursor': 'older'}


@pytest.mark.parametrize('options,expected_limit', [({}, 5), ({'limit': 3}, 3)])
def test_history_uses_read_client_and_closes_it(env, options, expected_limit):
    session = env.manager.associate(env.project, 'Existing', 'external-thread')
    clients = []
    original = env.manager.client_factory
    def factory(**kwargs):
        client = original(**kwargs)
        request = client.request
        def wrapped(method, params):
            if method == 'thread/turns/list':
                client.calls.append((method, params))
                return {'data': [], 'nextCursor': None}
            return request(method, params)
        client.request = wrapped
        clients.append(client)
        return client
    env.manager.client_factory = factory
    with Session(env.engine) as db:
        row = db.get(CodexSession, session['id'])
        assert env.manager.history(row, 'cursor-1', **options) == {'turns': [], 'next_cursor': None}
    assert clients[0].calls == [('thread/turns/list', {
        'threadId': 'external-thread', 'itemsView': 'summary', 'limit': expected_limit,
        'sortDirection': 'desc', 'cursor': 'cursor-1'})]
    assert clients[0].closed


@pytest.mark.parametrize('status,ephemeral', [('completed', True), ('failed', True), ('completed', False)])
def test_brief_generation_uses_isolated_thread_and_waits_for_final_event(env, status, ephemeral):
    clients = []

    class BriefClient(FakeClient):
        def __init__(self, **callbacks):
            super().__init__(**callbacks)
            clients.append(self)

        def request(self, method, params):
            result = super().request(method, params)
            if method == 'thread/start':
                result['thread']['ephemeral'] = ephemeral
            if method == 'turn/start':
                event = {'threadId': 'thread-1', 'turnId': 'turn-1'}
                for scope, phase, text in [
                    ({**event, 'threadId': 'other'}, 'final_answer', 'wrong thread'),
                    (event, 'final_answer', '{"brief": "saved"}'),
                    (event, 'commentary', 'ignore commentary'),
                ]:
                    self.on_event('item/completed', {
                        **scope, 'item': {'type': 'agentMessage', 'phase': phase, 'text': text},
                    })
                self.on_event('turn/completed', {
                    'threadId': 'thread-1', 'turn': {'id': 'turn-1', 'status': status},
                })
            return result

    env.manager.client_factory = BriefClient
    if status == 'completed' and ephemeral:
        assert env.manager.generate_brief('Synthetic input', {'type': 'object'}) == '{"brief": "saved"}'
    else:
        with pytest.raises(CodexError, match='ephemeral|did not complete'):
            env.manager.generate_brief('Synthetic input', {'type': 'object'})
    assert clients[0].closed
    assert [m for m, _ in clients[0].calls] == (
        ['account/read', 'thread/start'] + (['turn/start'] if ephemeral else []))
    start = clients[0].calls[1][1]
    assert start['ephemeral'] is True and start['sandbox'] == 'read-only'
    if ephemeral:
        turn = clients[0].calls[2][1]
        assert turn['outputSchema'] == {'type': 'object'}
        assert turn['sandboxPolicy']['networkAccess'] is False
