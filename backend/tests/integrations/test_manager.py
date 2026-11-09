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
        if method == 'config/read':
            return {'config': {'mcp_servers': {}}}
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


def test_generation_wait_is_configurable_and_timeout_is_explicit(monkeypatch):
    monkeypatch.delenv('PROJECTHUB_GENERATION_TIMEOUT', raising=False)
    assert module.generation_timeout() == 600
    monkeypatch.setenv('PROJECTHUB_GENERATION_TIMEOUT', '1200')
    assert module.generation_timeout() == 1200
    waited = []
    event = SimpleNamespace(wait=lambda seconds: waited.append(seconds) or False)
    with pytest.raises(CodexError, match='1200 seconds.*previous saved result'):
        module.wait_for_generation(event, module.generation_timeout())
    assert waited == [1200]
    for value in ('0', '-1', 'garbage', '3601'):
        monkeypatch.setenv('PROJECTHUB_GENERATION_TIMEOUT', value)
        with pytest.raises(CodexError, match='PROJECTHUB_GENERATION_TIMEOUT'):
            module.generation_timeout()


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


@pytest.mark.parametrize('status,ephemeral,model', [
    ('completed', True, None), ('completed', True, 'test-model'),
    ('failed', True, None), ('completed', False, None),
])
def test_brief_generation_uses_isolated_thread_and_waits_for_final_event(env, status, ephemeral, model):
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
                    ({**event, 'turnId': 'other-turn'}, 'final_answer', 'ignore another turn'),
                ]:
                    self.on_event('item/completed', {
                        **scope, 'item': {'type': 'agentMessage', 'phase': phase, 'text': text},
                    })
                self.on_event('turn/completed', {
                    'threadId': 'thread-1',
                    'turn': {'id': 'turn-1', 'status': status,
                             'error': {'codexErrorInfo': 'usageLimitExceeded', 'message': 'private diagnostic'}},
                })
                self.on_event('turn/completed', {
                    'threadId': 'thread-1', 'turn': {'id': 'other-turn', 'status': 'failed'},
                })
            return result

    env.manager.client_factory = BriefClient
    if status == 'completed' and ephemeral:
        assert env.manager.generate_brief(
            'Synthetic input', {'type': 'object'}, model=model, effort='high' if model else None,
        ) == '{"brief": "saved"}'
    else:
        with pytest.raises(CodexError, match='reached its usage limit' if ephemeral else 'ephemeral') as exc:
            env.manager.generate_brief('Synthetic input', {'type': 'object'})
        assert 'private diagnostic' not in str(exc.value)
    assert clients[0].closed
    assert [m for m, _ in clients[0].calls] == (
        ['account/read', 'thread/start'] + (['turn/start'] if ephemeral else []))
    start = clients[0].calls[1][1]
    assert start['ephemeral'] is True and start['sandbox'] == 'read-only'
    assert 'Be compact by default' in start['developerInstructions']
    assert start.get('model') == model
    if ephemeral:
        turn = clients[0].calls[2][1]
        assert turn['outputSchema'] == {'type': 'object'}
        assert turn.get('effort') == ('high' if model else None)
        assert turn['sandboxPolicy']['networkAccess'] is False


def test_ask_generation_refuses_inherited_connectors_before_turn(env, monkeypatch):
    client = FakeClient()
    client.connectors = True
    original = client.request

    def request(method, params):
        result = original(method, params)
        if method == 'thread/start':
            result['thread']['ephemeral'] = True
        return result

    monkeypatch.setattr(client, 'request', request)
    monkeypatch.setattr(env.manager, '_discussion_client', lambda cwd: client)
    with pytest.raises(CodexError, match='MCP'):
        env.manager.generate_brief('Synthetic question', {'type': 'object'}, verify_connectors=True)
    assert [method for method, _ in client.calls] == ['thread/start', 'mcpServerStatus/list']
    assert client.closed


@pytest.mark.parametrize('profile', [False, True])
def test_session_reader_reuses_one_transport_for_all_pages_and_metadata(env, tmp_path, monkeypatch, profile):
    from backend.app.integrations.codex import accounts

    home = tmp_path / 'codex-profile'
    home.mkdir()
    monkeypatch.setattr(accounts, 'account_homes', lambda: {'profile:test': home})
    clients = []

    class ReadClient:
        def __init__(self, **kwargs):
            self.home = kwargs.get('codex_home')
            self.calls = []
            self.closed = False
            clients.append(self)

        def request(self, method, params):
            self.calls.append((method, params))
            if method == 'thread/read':
                return {'thread': {'id': params['threadId'], 'status': {'type': 'idle'}}}
            assert method == 'thread/turns/list'
            return {'data': [], 'nextCursor': None}

        def close(self):
            self.closed = True

    monkeypatch.setattr(module, 'AppServerClient', ReadClient)
    env.manager.client_factory = ReadClient
    session = SimpleNamespace(id=10, project_id=1, thread_id='saved-thread',
                              codex_account='profile:test' if profile else None)
    with env.manager.session_reader(session) as reader:
        assert reader.identity == (10, 1, 'saved-thread', session.codex_account)
        reader.request('thread/read', {'threadId': 'saved-thread', 'includeTurns': False})
        for cursor in (None, 'page-2', 'page-3'):
            assert reader.history(session, cursor, client=reader, archive=True) == {'turns': [], 'next_cursor': None}
        assert len(clients) == 1
        assert not clients[0].closed
        assert clients[0].home == (str(home) if profile else None)
        assert [params.get('cursor') for method, params in clients[0].calls if method == 'thread/turns/list'] == [None, 'page-2', 'page-3']
        calls = len(clients[0].calls)
        foreign = SimpleNamespace(id=11, project_id=2, thread_id='foreign', codex_account=session.codex_account)
        with pytest.raises(CodexError, match='identity'):
            reader.history(foreign, None, client=reader, archive=True)
        with pytest.raises(CodexError, match='metadata'):
            reader.request('thread/read', {'threadId': 'foreign', 'includeTurns': False})
        with pytest.raises(CodexError, match='metadata'):
            reader.request('turn/start', {'threadId': 'saved-thread'})
        with pytest.raises(CodexError, match='cursor'):
            reader.history(session, 'x' * 8001, client=reader, archive=True)
        assert len(clients[0].calls) == calls
    assert clients[0].closed


def test_session_reader_closes_transport_on_failure(env, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(env.manager, '_new_read_client', lambda: client)
    session = SimpleNamespace(id=1, project_id=1, thread_id='saved', codex_account=None)
    with pytest.raises(CodexError, match='failed'):
        with env.manager.session_reader(session):
            raise CodexError('Read failed')
    assert client.closed


class ReusableBriefClient(FakeClient):
    def __init__(self, **callbacks):
        super().__init__(**callbacks)
        self.thread_count = self.close_count = 0
        self.fail = False
        self.release_supported = True

    def close(self):
        self.close_count += 1
        super().close()

    def request(self, method, params):
        result = super().request(method, params)
        if method == 'thread/start':
            self.thread_count += 1
            return {'thread': {'id': f'generated-{self.thread_count}', 'ephemeral': True}}
        if method == 'thread/unsubscribe':
            return {'status': 'unsubscribed'} if self.release_supported else {}
        if method == 'turn/start':
            thread = params['threadId']
            turn = f'turn-{self.thread_count}'
            # An old thread's final response cannot become this call's answer.
            self.on_event('item/completed', {
                'threadId': 'old-thread', 'turnId': turn,
                'item': {'type': 'agentMessage', 'text': 'wrong', 'phase': 'final_answer'},
            })
            self.on_event('item/completed', {
                'threadId': thread, 'turnId': turn,
                'item': {'type': 'agentMessage', 'text': f'answer-{self.thread_count}', 'phase': 'final_answer'},
            })
            self.on_event('turn/completed', {
                'threadId': thread, 'turn': {'id': turn, 'status': 'failed' if self.fail else 'completed'},
            })
            return {'turn': {'id': turn}}
        return result


def test_generation_session_reuses_transport_with_fresh_threads_and_releases_each(env):
    clients, probes = [], []
    def factory(**callbacks):
        clients.append(ReusableBriefClient(**callbacks))
        return clients[-1]
    original = env.manager._new_client
    def create(**callbacks):
        probes.append(True)
        return original(**callbacks)
    env.manager.client_factory = factory
    env.manager._new_client = create
    with env.manager.generation_session():
        assert clients == []
        assert env.manager.generate_brief('one', {}) == 'answer-1'
        assert not clients[0].closed
        assert env.manager.generate_brief('two', {}, model='chosen', effort='high') == 'answer-2'
        assert not clients[0].closed
        assert clients[0].on_event('item/completed', {}) is None
    assert len(clients) == len(probes) == 1
    assert clients[0].close_count == 1
    starts = [p for m, p in clients[0].calls if m == 'thread/start']
    assert len(starts) == 2 and all(p['ephemeral'] and p['sandbox'] == 'read-only' for p in starts)
    assert starts[1]['model'] == 'chosen'
    assert len([m for m, _ in clients[0].calls if m == 'account/read']) == 2
    assert [p for m, p in clients[0].calls if m == 'thread/unsubscribe'] == [
        {'threadId': 'generated-1'}, {'threadId': 'generated-2'},
    ]


def test_generation_session_failure_closes_and_does_not_retry(env):
    client = ReusableBriefClient()
    client.fail = True
    env.manager.client_factory = lambda **callbacks: (
        setattr(client, 'on_event', callbacks['on_event'])
        or setattr(client, 'on_disconnect', callbacks['on_disconnect']) or client)
    with env.manager.generation_session():
        with pytest.raises(CodexError, match='did not complete'):
            env.manager.generate_brief('failed', {})
        assert client.closed and client.close_count == 1
        with pytest.raises(CodexError, match='transport failed'):
            env.manager.generate_brief('no retry', {})
    assert len([m for m, _ in client.calls if m == 'turn/start']) == 1


def test_generation_session_does_not_share_transport_across_manager_identity(env):
    import copy
    clients = []
    def factory(**callbacks):
        clients.append(ReusableBriefClient(**callbacks))
        return clients[-1]
    env.manager.client_factory = factory
    other = copy.copy(env.manager)
    with env.manager.generation_session():
        assert env.manager.generate_brief('one', {}) == 'answer-1'
        with other.generation_session():
            assert other.generate_brief('other', {}) == 'answer-1'
        assert clients[1].closed and not clients[0].closed
        assert env.manager.generate_brief('two', {}) == 'answer-2'
    assert len(clients) == 2 and all(c.closed for c in clients)


def test_generation_session_closes_if_optional_thread_release_is_unsupported(env, caplog):
    clients = []
    def factory(**callbacks):
        client = ReusableBriefClient(**callbacks)
        client.release_supported = False
        clients.append(client)
        return client
    env.manager.client_factory = factory
    with env.manager.generation_session():
        assert env.manager.generate_brief('one', {}) == 'answer-1'
        assert clients[0].closed
        assert env.manager.generate_brief('two', {}) == 'answer-1'
    assert len(clients) == 2
    assert all(len([m for m, _ in c.calls if m == 'turn/start']) == 1 for c in clients)
    assert 'closing its transport' in caplog.text


def test_generation_scope_cannot_be_reopened_from_a_copied_context_after_exit(env):
    from contextvars import copy_context
    clients = []
    def factory(**callbacks):
        clients.append(ReusableBriefClient(**callbacks))
        return clients[-1]
    env.manager.client_factory = factory
    with env.manager.generation_session():
        context = copy_context()
    with pytest.raises(CodexError, match='scope has ended'):
        context.run(env.manager.generate_brief, 'late request', {})
    assert clients == []


@pytest.mark.parametrize('metadata,title,expected', [
    ({'name': 'Fix history links', 'preview': 'Original prompt'}, 'Codex discussion', 'Fix history links'),
    ({'preview': 'Original\nprompt'}, 'Codex discussion', 'Original prompt'),
    ({'name': '   ', 'preview': None}, 'Codex discussion', 'Codex discussion'),
    ({'name': 'Original title'}, 'My project session', 'My project session'),
    ({'name': 'x' * 250}, 'Codex discussion', 'x' * 200),
])
def test_link_session_uses_codex_name_and_preserves_custom_name(env, metadata, title, expected):
    client = FakeClient()
    original = client.request

    def request(method, params):
        result = original(method, params)
        if method == 'thread/read':
            result['thread'].update(metadata)
        return result

    client.request = request
    env.manager._new_read_client = lambda: client
    row = env.manager.associate(env.project, title, 'external-named-thread')
    assert row['title'] == expected
    assert row['thread_id'] == 'external-named-thread'
    assert client.closed
    assert client.calls == [('thread/read', {'threadId': 'external-named-thread', 'includeTurns': False})]
