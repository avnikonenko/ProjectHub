"""Actual SQLite persistence with a fake App Server; no FastAPI dependency."""
import json
from types import SimpleNamespace

import pytest
pytest.importorskip('sqlalchemy')
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from backend.app.models import Base, Project, Decision, CodexSession, Delivery, CodexEvent
from backend.app.integrations.codex import manager as module
from backend.app.integrations.codex.manager import CodexManager, context_preview, history_page
from backend.app.integrations.codex.protocol import CodexError, MissingSourceHistory


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


@pytest.mark.parametrize('notify', [False, True])
def test_resume_reports_transport_lost_during_callback_binding(env, monkeypatch, notify):
    sid, _ = session_and_delivery(env)
    bind = env.manager._bind_session_callbacks

    def bind_and_disconnect(client, session):
        bind(client, session)
        client.close()
        if notify:
            client.on_disconnect()

    with Session(env.engine) as db:
        session = db.get(CodexSession, sid)
        with monkeypatch.context() as patch:
            patch.setattr(env.manager, '_bind_session_callbacks', bind_and_disconnect)
            with pytest.raises(CodexError, match='disconnected while resuming'):
                env.manager.resume(session, env.project)
        db.refresh(session)
        assert session.status == 'interrupted'
        assert 'disconnected' in session.last_error
        assert sid not in env.manager.clients
        assert env.manager.resume(session, env.project)['status'] == 'idle'


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


@pytest.mark.parametrize('reader', ['direct', 'shared', 'archive'])
@pytest.mark.parametrize('title, missing_source', [
    ('Saved  design\n session', True), (' \n  ', True), ('Saved design session', False),
])
def test_history_identifies_session_for_missing_source_only(env, monkeypatch, reader, title, missing_source):
    saved = env.manager.associate(env.project, title, 'external-thread')
    client = FakeClient()
    failure = (MissingSourceHistory() if missing_source else
               CodexError('Codex rejected thread/turns/list (error -1)'))

    def request(method, params):
        assert method == 'thread/turns/list'
        assert params['threadId'] == saved['thread_id']
        raise failure from RuntimeError('Private server path /tmp/private-source.jsonl')

    monkeypatch.setattr(client, 'request', request)
    monkeypatch.setattr(env.manager, '_new_read_client', lambda: client)
    with Session(env.engine) as db:
        session = db.get(CodexSession, saved['id'])
        with pytest.raises(CodexError) as error:
            if reader == 'archive':
                with env.manager.session_reader(session) as archive:
                    archive.history(session, client=archive, archive=True)
            elif reader == 'shared':
                env.manager.history(session, client=client)
            else:
                env.manager.history(session)

    detail = str(error.value)
    assert '/tmp/private-source.jsonl' not in detail
    if missing_source:
        label = f"Session #{saved['id']}"
        if title.strip():
            label += ' “Saved design session”'
        assert f'{label} because' in detail
        assert 'unlink it in Sources' in detail
    else:
        assert error.value is failure
        assert detail == 'Codex rejected thread/turns/list (error -1)'
    assert client.closed == (reader != 'shared')


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
        ['account/read', 'config/read', 'thread/start']
        + (['mcpServerStatus/list', 'turn/start'] if ephemeral else []))
    start = next(params for method, params in clients[0].calls if method == 'thread/start')
    assert start['ephemeral'] is True and start['sandbox'] == 'read-only'
    assert 'Be compact by default' in start['developerInstructions']
    assert start.get('model') == model
    if ephemeral:
        turn = next(params for method, params in clients[0].calls if method == 'turn/start')
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


@pytest.mark.parametrize('failures', [1, 3])
def test_archive_formatting_recovery_reopens_pooled_codex_transport(env, failures):
    from backend.app.integrations.codex import archive

    clients, prompts = [], []

    class ArchiveClient(ReusableBriefClient):
        def request(self, method, params):
            if method != 'turn/start':
                return super().request(method, params)
            assert not self.closed
            self.calls.append((method, params))
            thread, turn = params['threadId'], f'turn-{self.thread_count}'
            prompt = params['input'][0]['text']
            prompts.append(prompt)
            if len(prompts) <= failures:
                self.on_event('item/agentMessage/delta', {'threadId': thread, 'turnId': turn,
                    'itemId': 'formatting-output', 'delta': '{' + '\n' * 4097})
            else:
                wire = json.loads(prompt.split('\n', 1)[1])
                source_id = wire['primary_ids'][0]
                answer = json.dumps({'evidence': [{'text': 'A recorded outcome.', 'source_ids': [source_id]}]})
                self.on_event('item/completed', {'threadId': thread, 'turnId': turn,
                    'item': {'type': 'agentMessage', 'phase': 'final_answer', 'text': answer}})
                self.on_event('turn/completed', {'threadId': thread, 'turn': {'id': turn, 'status': 'completed'}})
            return {'turn': {'id': turn}}

    def factory(**callbacks):
        clients.append(ArchiveClient(**callbacks))
        return clients[-1]

    def job(index):
        sources = [{'id': f'original:{index}:{i}', 'label': f'Turn {i}', 'session_id': index + 1,
                    'turn_id': str(i), 'text': f'An official excerpt {index}:{i}.'} for i in range(4)]
        return {'sources': sources, 'primary_ids': [source['id'] for source in sources], 'context_ids': []}, sources, 8

    env.manager.client_factory = factory
    with env.manager.generation_session(), archive.archive_worker_scope(1):
        results, hits, generated = archive._summary_jobs(
            env.engine, 1, env.manager, 2, job, 'conversation_overviews_v1')
        assert (hits, generated) == (0, 2)
        assert all(client.closed and client.close_count == 1 for client in clients[:-1])
        assert not clients[-1].closed
        calls = len(prompts)
        assert calls == (6 if failures == 3 else 3)
        assert 'Return compact JSON without indentation or padding.' in prompts[1]
        replay, hits, generated = archive._summary_jobs(
            env.engine, 1, env.manager, 2, job, 'conversation_overviews_v1')
        assert (hits, generated) == (2, 0)
        assert [result[0] for result in replay] == [result[0] for result in results]
        assert len(prompts) == calls
    assert len(clients) == failures + 1
    assert clients[-1].close_count == 1


@pytest.mark.parametrize('expire_at', ['transport setup', 'generation'])
def test_expired_optional_recovery_leaves_pooled_generation_usable(env, monkeypatch, expire_at):
    from backend.app.integrations.codex import manager, progress

    clients = []

    class Client(ReusableBriefClient):
        def request(self, method, params):
            if expire_at == 'transport setup' and len(clients) == 1 and method == 'config/read':
                raise progress.OptionalWorkExpired('Time reserved for synthesis.')
            return super().request(method, params)

    def factory(**callbacks):
        clients.append(Client(**callbacks))
        return clients[-1]

    env.manager.client_factory = factory
    wait = manager.wait_for_generation

    def expire_first(done, timeout):
        if expire_at == 'generation' and len(clients) == 1:
            raise progress.OptionalWorkExpired('Time reserved for synthesis.')
        return wait(done, timeout)

    monkeypatch.setattr(manager, 'wait_for_generation', expire_first)
    with env.manager.generation_session():
        with pytest.raises(progress.OptionalWorkExpired):
            env.manager.generate_brief('optional recovery', {})
        assert clients[0].closed
        assert env.manager.generate_brief('final synthesis', {}) == 'answer-1'
    assert len(clients) == 2


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


def test_generation_wait_wakes_quickly_for_cancellation():
    from threading import Event, Thread
    from time import monotonic
    from backend.app.integrations.codex import progress

    pid = 93004
    progress.begin(pid, 'brief')
    started = progress.read(pid, 'brief')['started_at']
    waiting = Event()
    done = Event()

    def cancel():
        assert waiting.wait(2)
        progress.cancel(pid, 'brief', started)

    thread = Thread(target=cancel)
    thread.start()
    before = monotonic()
    def wait(seconds):
        waiting.set()
        return done.wait(seconds)
    try:
        with pytest.raises(progress.GenerationCancelled):
            module.wait_for_generation(SimpleNamespace(wait=wait), 600)
        assert monotonic() - before < 1
    finally:
        thread.join(timeout=2)
        progress.finish(pid)


def test_cancelled_generation_interrupts_only_its_ephemeral_turn_and_closes_client(env):
    from backend.app.integrations.codex import progress

    pid = 93005
    clients = []
    progress.begin(pid, 'qa')
    started = progress.read(pid, 'qa')['started_at']

    class CancelClient(FakeClient):
        def __init__(self, **callbacks):
            super().__init__(**callbacks)
            clients.append(self)

        def request(self, method, params):
            result = super().request(method, params)
            if method == 'thread/start':
                result['thread']['ephemeral'] = True
            if method == 'turn/start':
                progress.cancel(pid, 'qa', started)
            return result

    env.manager.client_factory = CancelClient
    try:
        with env.manager.generation_session():
            with pytest.raises(progress.GenerationCancelled):
                env.manager.generate_brief('Synthetic request', {'type': 'object'})
            assert clients[0].closed
        calls = [(method, params) for method, params in clients[0].calls if method == 'turn/interrupt']
        assert calls == [('turn/interrupt', {'threadId': 'thread-1', 'turnId': 'turn-1'})]
        assert not any(method == 'thread/resume' for method, _ in clients[0].calls)
    finally:
        progress.finish(pid)
    assert progress.read(pid, 'qa')['state'] == 'cancelled'


def test_cancel_before_generation_creates_no_transport(env):
    from backend.app.integrations.codex import progress

    pid = 93006
    progress.begin(pid, 'scheme')
    progress.cancel(pid, 'scheme', progress.read(pid, 'scheme')['started_at'])
    env.manager.client_factory = lambda **kwargs: pytest.fail('Cancelled generation opened a transport')
    try:
        with pytest.raises(progress.GenerationCancelled):
            env.manager.generate_brief('Synthetic', {})
    finally:
        progress.finish(pid)


def test_cancelled_delivery_cannot_be_sent_or_automatically_retried(env):
    session_id, delivery_id = session_and_delivery(env)
    with Session(env.engine) as db:
        db.get(Delivery, delivery_id).status = 'cancelled'
        db.commit()
    client = env.manager.clients[session_id]
    before = list(client.calls)
    with pytest.raises(CodexError, match='cancelled'):
        env.manager.send(delivery_id, reconnect=True)
    assert client.calls == before
    with Session(env.engine) as db:
        assert db.get(Delivery, delivery_id).status == 'cancelled'


@pytest.mark.parametrize('has_owned_turn', [False, True])
def test_interrupt_never_uses_a_foreign_project_delivery(env, has_owned_turn):
    sid, did = session_and_delivery(env)
    with Session(env.engine) as db:
        owned = db.get(Delivery, did)
        owned.turn_id = 'owned-turn' if has_owned_turn else None
        db.add(Delivery(project_id=2, session_id=sid, content='Foreign record', turn_id='foreign-turn'))
        db.commit()
        session = db.get(CodexSession, sid)
        if has_owned_turn:
            env.manager.interrupt(session)
        else:
            with pytest.raises(CodexError, match='No known turn'):
                env.manager.interrupt(session)
    interrupts = [params for method, params in env.manager.clients[sid].calls if method == 'turn/interrupt']
    assert interrupts == ([{'threadId': session.thread_id, 'turnId': 'owned-turn'}] if has_owned_turn else [])


@pytest.mark.parametrize('case', ['runaway', 'incomplete-runaway', 'late-runaway', 'boundary', 'incomplete-string', 'other-thread', 'other-turn', 'separate-items'])
def test_generation_whitespace_guard_respects_strings_and_item_identity(env, case, monkeypatch):
    clients = []
    answer = '{"ok":true}'

    class FormattingClient(FakeClient):
        def __init__(self, **callbacks):
            super().__init__(**callbacks)
            clients.append(self)

        def request(self, method, params):
            result = super().request(method, params)
            if method == 'thread/start':
                result['thread']['ephemeral'] = True
            if method == 'turn/start':
                scope = {'threadId': 'thread-1', 'turnId': 'turn-1', 'itemId': 'item-1'}

                def delta(text, **changes):
                    self.on_event('item/agentMessage/delta', {**scope, **changes, 'delta': text})

                if case == 'late-runaway':
                    return result
                if case in ('runaway', 'incomplete-runaway'):
                    delta('{' if case == 'incomplete-runaway' else answer)
                    delta('\n' * 4096)
                    delta(' ')
                    # No official completion is fabricated for the failure.
                    return result
                if case == 'boundary':
                    delta(answer)
                    delta('\n' * 4096)
                elif case == 'incomplete-string':
                    delta('{"text":"')
                    delta(' ' * 6000)
                    delta('still inside a JSON string"}')
                elif case == 'other-thread':
                    delta(answer + '\n' * 5000, threadId='foreign-thread')
                elif case == 'other-turn':
                    delta(answer + '\n' * 5000, turnId='foreign-turn')
                elif case == 'separate-items':
                    delta('{"text":"')
                    delta('"}' + '\n' * 5000, itemId='different-item')
                self.on_event('item/completed', {**scope, 'item': {
                    'type': 'agentMessage', 'phase': 'final_answer', 'text': answer,
                }})
                self.on_event('turn/completed', {'threadId': scope['threadId'],
                    'turn': {'id': scope['turnId'], 'status': 'completed'}})
            return result

    env.manager.client_factory = FormattingClient
    if case == 'late-runaway':
        def wait(done, timeout):
            assert not done.is_set()
            clients[0].on_event('item/agentMessage/delta', {'threadId': 'thread-1',
                'turnId': 'turn-1', 'itemId': 'item-1', 'delta': answer + '\n' * 4097})
            assert done.is_set()
        monkeypatch.setattr(module, 'wait_for_generation', wait)
    if case in ('runaway', 'incomplete-runaway', 'late-runaway'):
        with pytest.raises(CodexError, match='excessive whitespace.*Generation stopped'):
            env.manager.generate_brief('Synthetic source', {'type': 'object'})
    else:
        assert env.manager.generate_brief('Synthetic source', {'type': 'object'}) == answer
    assert clients[0].closed


def test_generation_streamed_json_never_substitutes_for_official_completion(env, monkeypatch):
    class IncompleteClient(FakeClient):
        def request(self, method, params):
            result = super().request(method, params)
            if method == 'thread/start':
                result['thread']['ephemeral'] = True
            if method == 'turn/start':
                self.on_event('item/agentMessage/delta', {'threadId': 'thread-1',
                    'turnId': 'turn-1', 'itemId': 'item-1', 'delta': '{"ok":true}  '})
            return result

    def wait(done, timeout):
        assert not done.is_set()
        raise CodexError('Still waiting for official completion')

    monkeypatch.setattr(module, 'wait_for_generation', wait)
    env.manager.client_factory = IncompleteClient
    with pytest.raises(CodexError, match='official completion'):
        env.manager.generate_brief('Synthetic source', {'type': 'object'})


def test_json_formatting_guard_has_no_content_size_limit_or_buffer():
    guard = module._JsonFormattingGuard()
    large_json = '{"text":"' + 'x' * 70_000 + '"}'
    assert not guard.feed(large_json)
    assert not guard.disabled
    assert all(isinstance(value, (bool, int)) for value in vars(guard).values())
    assert guard.feed('\n' * 4097)


def test_json_formatting_guard_tracks_escaped_quotes_across_chunks():
    guard = module._JsonFormattingGuard()
    assert not guard.feed('{"text":"escaped quote: ' + chr(92))
    assert not guard.feed('"' + ' ' * 6000)
    assert guard.quoted and not guard.escaped
    assert not guard.feed('and escaped backslash: ' + chr(92))
    assert not guard.feed(chr(92) + ' ' * 6000)
    assert guard.quoted and not guard.escaped
    assert not guard.feed('end", "next":')
    assert guard.feed('\t' * 4097)


def test_json_formatting_guard_ignores_leading_whitespace_and_nonobject_streams():
    guard = module._JsonFormattingGuard()
    assert not guard.feed(' ' * 6000)
    assert not guard.feed('{"ok":true}')
    assert guard.feed(' ' * 4097)
    other = module._JsonFormattingGuard()
    assert not other.feed('[{}]' + ' ' * 6000)
    assert other.disabled


def test_cancellation_helper_start_failure_keeps_cancelled_error_and_closes_private_client(env, monkeypatch):
    from backend.app.integrations.codex import progress

    pid = 93010
    clients = []
    progress.begin(pid, 'qa')
    started = progress.read(pid, 'qa')['started_at']

    class CancelClient(FakeClient):
        def __init__(self, **callbacks):
            super().__init__(**callbacks)
            clients.append(self)

        def request(self, method, params):
            result = super().request(method, params)
            if method == 'thread/start':
                result['thread']['ephemeral'] = True
            if method == 'turn/start':
                progress.cancel(pid, 'qa', started)
            return result

    def fail_start(self):
        raise RuntimeError('thread capacity exhausted')

    monkeypatch.setattr(module.threading.Thread, 'start', fail_start)
    env.manager.client_factory = CancelClient
    try:
        with env.manager.generation_session():
            with pytest.raises(progress.GenerationCancelled):
                env.manager.generate_brief('Synthetic request', {'type': 'object'})
        assert clients[0].closed
        assert not any(method in ('thread/resume', 'turn/interrupt') for method, _ in clients[0].calls)
    finally:
        progress.finish(pid)
    assert progress.read(pid, 'qa')['state'] == 'cancelled'
