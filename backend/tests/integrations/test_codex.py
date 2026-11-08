"""Isolated App Server tests use a fake client; no model calls or credentials."""
import json
from types import SimpleNamespace

import pytest
pytest.importorskip('sqlalchemy')
pytest.importorskip('fastapi')
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from backend.app.models import Base, Project, Decision, CodexSession, Delivery, ContextPackage, CodexEvent, AuditEvent
from backend.app.integrations.codex import manager as module
from backend.app.integrations.codex.manager import CodexManager, context_preview
from backend.app.integrations.codex.protocol import CodexError
from backend.app.integrations.codex.router import router


class FakeClient:
    next_id = 0
    complete_early = False
    connectors = False
    reject = False
    account_type = 'chatgpt'
    configured = ()

    def __init__(self, on_event=None, on_disconnect=None, disabled_mcp=()):
        self.on_event = on_event or (lambda *a: None)
        self.on_disconnect = on_disconnect or (lambda: None)
        self.calls = []
        self.closed = False
        self.disabled_mcp = disabled_mcp

    def request(self, method, params):
        self.calls.append((method, params))
        if method == 'account/read':
            return {'account': {'type': self.account_type}}
        if method == 'config/read':
            return {'config': {'mcp_servers': {name: {} for name in self.configured}}}
        if method == 'thread/start':
            FakeClient.next_id += 1
            return {'thread': {'id': f'thread-{FakeClient.next_id}', 'status': {'type': 'idle'}}}
        if method in ('thread/resume', 'thread/read'):
            return {'thread': {'id': params['threadId'], 'status': {'type': 'idle'}}}
        if method == 'thread/turns/list':
            return {'data': [{
                'id': 'saved-turn', 'status': 'completed', 'items': [
                    {'type': 'userMessage', 'content': [{'type': 'text', 'text': 'Saved question'}]},
                    {'type': 'agentMessage', 'text': 'Saved answer'},
                ],
            }], 'nextCursor': None}
        if method == 'mcpServerStatus/list':
            return {'data': [{'name': name, 'tools': {'run': {}} if self.connectors else {},
                              'resources': [], 'resourceTemplates': [], 'serverCapabilities': None}
                             for name in (self.configured or (('inherited',) if self.connectors else ()))],
                    'nextCursor': None}
        if method == 'turn/start':
            if self.reject:
                raise CodexError('Codex request timed out; delivery may be unverified')
            self.on_event('turn/started', {'turn': {'id': 'turn-1', 'status': 'inProgress'}})
            if self.complete_early:
                self.on_event('turn/completed', {'turn': {'id': 'turn-1', 'status': 'completed'}})
            return {'turn': {'id': 'turn-1'}}
        return {}

    def close(self):
        self.closed = True


@pytest.fixture
def setup(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'db.sqlite'), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(module, 'availability', lambda *_: {
        'installed': True, 'app_server': True, 'authenticated': True, 'error': ''})
    app = FastAPI()
    app.state.engine, app.state.data_dir = engine, tmp_path
    manager = CodexManager(engine, tmp_path, client_factory=FakeClient)
    app.state.codex_manager = manager
    app.include_router(router)
    FakeClient.next_id, FakeClient.complete_early, FakeClient.connectors, FakeClient.reject = 0, False, False, False
    FakeClient.account_type = 'chatgpt'
    FakeClient.configured = ()
    with Session(engine) as db:
        a, b = Project(name='A'), Project(name='B')
        db.add_all([a, b]); db.commit()
        db.add_all([Decision(project_id=1, title='Chosen', status='accepted', description='Use SQLite'),
                    Decision(project_id=1, title='Hypothesis', status='proposed'),
                    Decision(project_id=2, title='Private B', status='accepted')])
        db.commit()
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, manager=manager, engine=engine, path=tmp_path)
    manager.close()
    engine.dispose()


def create_session(env):
    response = env.client.post('/api/projects/1/codex/sessions', json={'title': 'Review'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def test_session_rename_is_local_scoped_validated_and_audited(setup):
    sid = create_session(setup)
    client = setup.manager.clients[sid]
    previous_calls = list(client.calls)
    path = f'/api/projects/1/codex/sessions/{sid}'
    original = setup.client.get('/api/projects/1/codex/sessions').json()[0]
    for title in ('', '   ', 'x' * 201, None):
        assert setup.client.patch(path, json={'title': title}).status_code == 422
    assert setup.client.patch(path, json={'title': 'Title', 'thread_id': 'other'}).status_code == 422
    assert setup.client.patch(f'/api/projects/2/codex/sessions/{sid}', json={'title': 'Foreign'}).status_code == 404
    renamed = setup.client.patch(path, json={'title': '  Database design  '})
    assert renamed.status_code == 200
    assert renamed.json()['title'] == 'Database design'
    current = setup.client.get('/api/projects/1/codex/sessions').json()[0]
    assert current['title'] == 'Database design'
    assert current['thread_id'] == original['thread_id']
    assert current['last_activity_at'] == original['last_activity_at']
    assert client.calls == previous_calls
    with Session(setup.engine) as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == 'session_renamed'))
        assert event.project_id == 1 and event.entity_id == sid
        assert event.before['title'] == original['title']
        assert event.after['title'] == 'Database design'


def test_session_topic_can_be_set_and_cleared_without_renaming(setup):
    sid = create_session(setup)
    path = f'/api/projects/1/codex/sessions/{sid}'
    for payload in ({}, {'topic': None}, {'topic': 'x' * 81}, {'codex_activity_at': '2026-01-01'}):
        assert setup.client.patch(path, json=payload).status_code == 422
    assert setup.client.patch(f'/api/projects/2/codex/sessions/{sid}', json={'topic': 'Private'}).status_code == 404
    result = setup.client.patch(path, json={'topic': '  Database  '})
    assert result.status_code == 200
    assert result.json()['title'] == 'Review'
    assert result.json()['topic'] == 'Database'
    assert setup.client.get('/api/projects/1/codex/sessions').json()[0]['topic'] == 'Database'
    result = setup.client.patch(path, json={'topic': '', 'title': 'Next review'})
    assert result.status_code == 200
    assert result.json()['topic'] == '' and result.json()['title'] == 'Next review'
    with Session(setup.engine) as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == 'session_details_updated').order_by(AuditEvent.id))
        assert event.before['topic'] == '' and event.after['topic'] == 'Database'


def test_session_activity_is_local_observed_scoped_and_stably_ordered(setup):
    with Session(setup.engine) as db:
        first = CodexSession(project_id=1, thread_id='first', created_at='2026-01-01T00:00:00+00:00')
        second = CodexSession(project_id=1, thread_id='second', created_at='2026-01-02T00:00:00+00:00')
        foreign = CodexSession(project_id=2, thread_id='foreign', created_at='2026-01-03T00:00:00+00:00')
        db.add_all([first, second, foreign])
        db.flush()
        ids = first.id, second.id, foreign.id
        db.add_all([
            Delivery(project_id=1, session_id=first.id, content='Question', created_at='2026-02-01T00:00:00+00:00'),
            CodexEvent(project_id=1, session_id=first.id, method='item/completed', payload={},
                       created_at='2026-03-01T00:00:00+00:00'),
            CodexEvent(project_id=2, session_id=foreign.id, method='item/completed', payload={},
                       created_at='2026-04-01T00:00:00+00:00'),
            # Even a malformed cross-project relation must not affect the visible activity.
            Delivery(project_id=2, session_id=first.id, content='Foreign', created_at='2026-05-01T00:00:00+00:00'),
        ])
        db.commit()
    rows = setup.client.get('/api/projects/1/codex/sessions').json()
    assert [row['id'] for row in rows] == list(ids[:2])
    assert [row['last_activity_at'] for row in rows] == [
        '2026-03-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00',
    ]
    assert setup.client.get('/api/projects/999/codex/sessions').status_code == 404


def test_context_is_deterministic_scoped_and_has_version(setup):
    a = setup.client.get('/api/projects/1/codex/context').json()
    b = setup.client.get('/api/projects/1/codex/context').json()
    assert a == b
    assert len(a['version']) == 64
    assert 'Chosen' in a['content']
    assert 'Hypothesis' not in a['content'] and 'Private B' not in a['content']


def test_draft_requires_explicit_confirmation_and_is_idempotent(setup):
    sid = create_session(setup)
    draft = setup.client.post('/api/projects/1/codex/deliveries', json={'decision_id': 1, 'session_id': sid}).json()
    client = setup.manager.clients[sid]
    assert not any(method == 'turn/start' for method, _ in client.calls)
    url = f"/api/projects/1/codex/deliveries/{draft['id']}/send"
    assert setup.client.post(url, json={}).status_code == 422
    assert setup.client.post(url, json={'confirmed': True}).json()['status'] == 'sent'
    assert setup.client.post(url, json={'confirmed': True}).json()['status'] == 'sent'
    assert sum(method == 'turn/start' for method, _ in client.calls) == 1
    turn = next(params for method, params in client.calls if method == 'turn/start')
    assert turn['sandboxPolicy'] == {'type': 'readOnly', 'networkAccess': False}
    assert turn['approvalPolicy'] == 'never'


def test_proposed_decision_and_cross_project_links_rejected(setup):
    sid = create_session(setup)
    assert setup.client.post('/api/projects/1/codex/deliveries', json={'decision_id': 2, 'session_id': sid}).status_code == 409
    assert setup.client.post('/api/projects/1/codex/deliveries', json={'decision_id': 3, 'session_id': sid}).status_code == 404
    assert setup.client.get(f'/api/projects/2/codex/sessions/{sid}/events').status_code == 404
    assert setup.client.post(f'/api/projects/2/codex/sessions/{sid}/turns', json={'prompt': 'review'}).status_code == 404


def test_changed_decision_requires_new_preview(setup):
    sid = create_session(setup)
    draft = setup.client.post('/api/projects/1/codex/deliveries', json={'decision_id': 1, 'session_id': sid}).json()
    with Session(setup.engine) as db:
        db.get(Decision, 1).rationale = 'new evidence'
        db.commit()
    assert setup.client.post(f"/api/projects/1/codex/deliveries/{draft['id']}/send", json={'confirmed': True}).status_code == 409


def test_busy_queue_does_not_automatically_send(setup):
    sid = create_session(setup)
    first = setup.client.post(f'/api/projects/1/codex/sessions/{sid}/turns', json={'prompt': 'first'}).json()
    second = setup.client.post(f'/api/projects/1/codex/sessions/{sid}/turns', json={'prompt': 'second'}).json()
    assert first['status'] == 'sent' and second['status'] == 'queued'
    client = setup.manager.clients[sid]
    client.on_event('turn/completed', {'turn': {'id': 'turn-1', 'status': 'completed'}})
    with Session(setup.engine) as db:
        assert db.get(Delivery, second['id']).status == 'queued'
    assert sum(method == 'turn/start' for method, _ in client.calls) == 1


def test_completion_before_response_is_preserved(setup):
    sid = create_session(setup)
    setup.manager.clients[sid].complete_early = True
    response = setup.client.post(f'/api/projects/1/codex/sessions/{sid}/turns', json={'prompt': 'review'}).json()
    assert response['status'] == 'completed'
    events = setup.client.get(f'/api/projects/1/codex/sessions/{sid}/events').json()
    assert events['session']['status'] == 'idle'
    assert any(event['method'] == 'turn/completed' for event in events['events'])
    with Session(setup.engine) as db:
        package = db.get(ContextPackage, response['context_id'])
        assert package.version in [context_preview(db, 1)['version']]


def test_ambiguous_error_never_resends_implicitly(setup):
    sid = create_session(setup)
    setup.manager.clients[sid].reject = True
    response = setup.client.post(f'/api/projects/1/codex/sessions/{sid}/turns', json={'prompt': 'review'}).json()
    assert response['status'] == 'unverified'
    retry = setup.client.post(f"/api/projects/1/codex/deliveries/{response['id']}/send", json={'confirmed': True})
    assert retry.status_code == 409


def test_inherited_connectors_fail_closed_before_generation(setup):
    sid = create_session(setup)
    client = setup.manager.clients[sid]
    client.connectors = True
    response = setup.client.post(f'/api/projects/1/codex/sessions/{sid}/turns', json={'prompt': 'review'}).json()
    assert response['status'] == 'failed'
    assert not any(method == 'turn/start' for method, _ in client.calls)


def test_existing_session_keeps_working_after_project_account_changes(setup, monkeypatch):
    from backend.app.integrations.codex import accounts

    first, second = setup.path / 'first', setup.path / 'second'
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(accounts, 'account_homes', lambda: {'profile:first': first, 'profile:second': second})

    def client_factory(*, codex_home, **kwargs):
        client = FakeClient(**kwargs)
        client.home = codex_home
        return client

    monkeypatch.setattr(module, 'AppServerClient', client_factory)
    base = '/api/projects/1/codex'
    assert setup.client.patch(base + '/account', json={'account': 'profile:first'}).status_code == 200
    sid = create_session(setup)
    owner = next(iter(setup.manager.account_managers.values()))
    assert owner.clients[sid].home == str(first)
    assert setup.client.patch(base + '/account', json={'account': 'profile:second'}).status_code == 200
    assert not owner.clients[sid].closed
    assert setup.client.post(f'{base}/sessions/{sid}/resume').status_code == 200
    assert owner.clients[sid].home == str(first)
    assert setup.client.post(f'/api/projects/2/codex/sessions/{sid}/resume').status_code == 404
    result = setup.client.post(f'{base}/sessions/{sid}/turns', json={'prompt': 'review'})
    assert result.status_code == 201 and result.json()['status'] == 'sent'
    new_id = create_session(setup)
    assert any(child.clients.get(new_id) and child.clients[new_id].home == str(second)
               for child in setup.manager.account_managers.values())


def test_explicit_review_requests_reconnect_disconnected_session(setup):
    sid = create_session(setup)
    base = '/api/projects/1/codex'
    setup.manager.clients[sid].close()
    response = setup.client.post(f'{base}/sessions/{sid}/turns', json={'prompt': 'review'})
    assert response.status_code == 201 and response.json()['status'] == 'sent'
    client = setup.manager.clients[sid]
    assert [method for method, _ in client.calls].count('thread/resume') == 1
    assert [method for method, _ in client.calls].count('turn/start') == 1
    sent = next(params for method, params in client.calls if method == 'turn/start')
    assert 'Be compact by default' in sent['input'][0]['text']

    queued = setup.client.post(f'{base}/sessions/{sid}/turns', json={'prompt': 'another review'}).json()
    assert queued['status'] == 'queued'
    with Session(setup.engine) as db:
        db.get(CodexSession, sid).status = 'idle'
        db.commit()
    setup.manager.clients.pop(sid).close()
    result = setup.client.post(f"{base}/deliveries/{queued['id']}/send", json={'confirmed': True})
    assert result.status_code == 200 and result.json()['status'] == 'sent'
    assert [method for method, _ in setup.manager.clients[sid].calls].count('turn/start') == 1


def test_discussion_disables_configured_connectors_before_sending(setup, monkeypatch):
    monkeypatch.setattr(FakeClient, 'configured', ('inherited',))
    sid = create_session(setup)
    client = setup.manager.clients[sid]
    assert client.disabled_mcp == ['inherited']
    url = f'/api/projects/1/codex/sessions/{sid}/turns'
    client.connectors = True
    failed = setup.client.post(url, json={'prompt': 'review'}).json()
    assert failed['status'] == 'failed'
    assert not any(method == 'turn/start' for method, _ in client.calls)
    resumed = setup.client.post(f'/api/projects/1/codex/sessions/{sid}/resume')
    assert resumed.status_code == 200
    replacement = setup.manager.clients[sid]
    assert client.closed and replacement.disabled_mcp == ['inherited']
    retried = setup.client.post(f"/api/projects/1/codex/deliveries/{failed['id']}/send",
                                json={'confirmed': True}).json()
    assert retried['status'] == 'sent'


def test_restart_preserves_uncertainty(setup):
    sid = create_session(setup)
    with Session(setup.engine) as db:
        db.get(CodexSession, sid).status = 'running'
        d = Delivery(project_id=1, session_id=sid, content='pending', status='sending')
        db.add(d); db.commit(); did = d.id
    fresh = CodexManager(setup.engine, setup.path, client_factory=FakeClient)
    with Session(setup.engine) as db:
        assert db.get(CodexSession, sid).status == 'interrupted'
        assert db.get(Delivery, did).status == 'unverified'
    assert fresh.clients == {}


def test_unavailable_codex_reports_failure_without_losing_core(setup, monkeypatch):
    monkeypatch.setattr(module, 'availability', lambda *_: {'installed': False, 'app_server': False,
                        'authenticated': False, 'error': 'Codex unavailable'})
    response = setup.client.post('/api/projects/1/codex/sessions', json={})
    assert response.status_code == 503
    assert setup.client.get('/api/projects/1/codex/context').status_code == 200


def test_api_key_auth_is_refused_before_thread_creation(setup):
    FakeClient.account_type = 'apiKey'
    response = setup.client.post('/api/projects/1/codex/sessions', json={})
    assert response.status_code == 503
    assert 'fallback is disabled' in response.json()['detail']


def test_linked_session_history_is_read_only_and_project_scoped(setup):
    response = setup.client.post('/api/projects/1/codex/sessions', json={
        'title': 'Existing', 'thread_id': 'external-thread'})
    assert response.status_code == 201, response.text
    session = response.json()
    assert session['status'] == 'linked'
    assert session['id'] not in setup.manager.clients
    history = setup.client.get(
        f"/api/projects/1/codex/sessions/{session['id']}/history").json()
    assert history['turns'][0]['messages'] == [
        {'role': 'user', 'text': 'Saved question'},
        {'role': 'assistant', 'text': 'Saved answer'},
    ]
    assert setup.client.get(
        f"/api/projects/2/codex/sessions/{session['id']}/history").status_code == 404
    assert setup.client.post(
        f"/api/projects/1/codex/sessions/{session['id']}/turns",
        json={'prompt': 'do not send'}).status_code == 409
    assert setup.client.post('/api/projects/1/codex/deliveries', json={
        'decision_id': 1, 'session_id': session['id']}).status_code == 409
    with Session(setup.engine) as db:
        assert not list(db.scalars(select(Delivery).where(Delivery.session_id == session['id'])))
        assert not list(db.scalars(select(ContextPackage).where(ContextPackage.session_id == session['id'])))


def test_session_sync_pause_is_scoped_audited_and_preserves_records(setup):
    from backend.app.models import AuditEvent
    sid = create_session(setup)
    prefix = '/api/projects/1/codex'
    client = setup.manager.clients[sid]
    created = setup.client.post(f'{prefix}/sessions/{sid}/turns', json={'prompt': 'Review later'}).json()
    calls = list(client.calls)
    url = f'{prefix}/sessions/{sid}/sync'
    assert setup.client.patch(f'/api/projects/2/codex/sessions/{sid}/sync',
                              json={'enabled': False}).status_code == 404
    assert setup.client.patch(url, json={'enabled': 'false'}).status_code == 422
    paused = setup.client.patch(url, json={'enabled': False})
    assert paused.status_code == 200 and paused.json()['auto_sync'] is False
    assert client.calls == calls
    assert any(s['id'] == sid and s['auto_sync'] is False
               for s in setup.client.get(prefix + '/sessions').json())
    events = setup.client.get(f'{prefix}/sessions/{sid}/events').json()
    assert any(d['id'] == created['id'] for d in events['deliveries'])
    with Session(setup.engine) as db:
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == 'session_sync_changed'))
        assert audit.before['auto_sync'] is True and audit.after['auto_sync'] is False
        row = db.get(CodexSession, sid)
        assert row.auto_sync is False
    assert setup.client.patch(url, json={'enabled': True}).json()['auto_sync'] is True


def test_project_ai_instructions_are_scoped_and_used_for_review(setup):
    base = '/api/projects/1/codex'
    route = base + '/instructions'
    assert 'Be compact by default' in setup.client.get(route).json()['instructions']
    assert setup.client.patch(route, json={'instructions': 'x' * 16003}).status_code == 422
    assert setup.client.patch('/api/projects/999/codex/instructions', json={'instructions': 'Unknown'}).status_code == 404
    text = 'Use British English.\nStart with the answer.'
    assert setup.client.patch(route, json={'instructions': text}).json() == {'instructions': text}
    assert setup.client.get(route).json() == {'instructions': text}
    assert 'Be compact by default' in setup.client.get('/api/projects/2/codex/instructions').json()['instructions']
    sid = create_session(setup)
    response = setup.client.post(f'{base}/sessions/{sid}/turns', json={'prompt': 'Review the project'})
    assert response.status_code == 201
    params = next(params for method, params in setup.manager.clients[sid].calls if method == 'turn/start')
    assert text in params['input'][0]['text']
    assert 'Be compact by default' not in params['input'][0]['text']
    assert 'Remain read-only' in params['input'][0]['text']
    assert params['sandboxPolicy']['type'] == 'readOnly'
    assert text not in response.json()['content']
    assert setup.client.patch(route, json={'instructions': ''}).json() == {'instructions': ''}
    assert setup.client.get(route).json() == {'instructions': ''}


def test_generation_routes_use_project_instructions_without_leaking_scope(setup, monkeypatch):
    from importlib import import_module
    from backend.app.integrations.codex.ai_instructions import ai_instructions

    routes = import_module('backend.app.integrations.codex.router')
    seen = []

    def generate(*args):
        seen.append(ai_instructions())
        return {'ok': True}

    monkeypatch.setattr(routes, 'refresh_brief', generate)
    monkeypatch.setattr(routes, 'refresh_qa', generate)
    monkeypatch.setattr(routes, 'answer_project', generate)
    monkeypatch.setattr(routes, 'retrieve_passages', lambda *args: [{'text': 'Evidence'}])
    text = 'Use the terminology from this project.'
    assert setup.client.patch('/api/projects/1/codex/instructions', json={'instructions': text}).status_code == 200
    for pid in (1, 2):
        for kind in ('brief', 'qa', 'ask'):
            body = {'confirmed': True, **({'question': 'What changed?'} if kind == 'ask' else {})}
            response = setup.client.post(f'/api/projects/{pid}/codex/{kind}', json=body)
            assert response.status_code == 200, response.text
    assert len(seen) == 6
    assert all(text in instructions for instructions in seen[:3])
    assert all(text not in instructions for instructions in seen[3:])


def test_register_codex_directory_persists_routes_and_deduplicates(setup, tmp_path, monkeypatch):
    from importlib import import_module
    from backend.app.integrations.codex import accounts
    from backend.app.models import CodexProfile

    routes = import_module('backend.app.integrations.codex.router')
    home = tmp_path / 'registered-home'
    home.mkdir()
    sentinel = home / 'auth.json'
    sentinel.write_text('private sentinel, never read by ProjectHub')
    probes = []

    def availability(path):
        probes.append(path)
        return {'authenticated': True, 'app_server': True, 'error': ''}

    monkeypatch.setattr(routes, 'availability', availability)
    base = '/api/projects/1/codex'
    result = setup.client.post(base + '/accounts', json={'name': 'Work profile', 'directory': str(home)})
    assert result.status_code == 200, result.text
    data = result.json()
    key = data['registered_id']
    assert key.startswith('registered:') and data['added']
    assert data['account'] is None
    assert {'id': key, 'label': 'Work profile'} in data['options']
    assert 'private sentinel' not in result.text
    duplicate = setup.client.post(base + '/accounts', json={'name': 'Duplicate', 'directory': str(home / '.')})
    assert duplicate.status_code == 200 and duplicate.json()['registered_id'] == key
    assert not duplicate.json()['added']
    with Session(setup.engine) as db:
        assert len(list(db.scalars(select(CodexProfile)))) == 1
    assert accounts.account_home(key, setup.engine) == str(home.resolve())
    assert setup.client.patch(base + '/account', json={'account': key}).status_code == 200
    assert setup.client.get('/api/projects/2/codex/account').json()['account'] is None
    child = module.manager_for(SimpleNamespace(state=SimpleNamespace(
        engine=setup.engine, data_dir=setup.path, codex_manager=setup.manager)), 1)
    assert child.client_factory.keywords['codex_home'] == str(home)
    assert sentinel.read_text() == 'private sentinel, never read by ProjectHub'
    assert probes == [str(home), str(home)]


def test_codex_directory_rejects_missing_and_unlogged_profiles_before_registering(setup, tmp_path, monkeypatch):
    from importlib import import_module
    from backend.app.models import CodexProfile

    routes = import_module('backend.app.integrations.codex.router')
    probes = []

    def availability(path):
        probes.append(path)
        return {'authenticated': False, 'app_server': True, 'error': 'Existing ChatGPT login required.'}

    monkeypatch.setattr(routes, 'availability', availability)
    base = '/api/projects/1/codex/accounts'
    for directory in ('relative/path', str(tmp_path / 'missing'), '/'):
        assert setup.client.post(base, json={'name': 'Invalid', 'directory': directory}).status_code == 422
    assert not probes
    home = tmp_path / 'logged-out'; home.mkdir()
    assert setup.client.post('/api/projects/999/codex/accounts', json={'name': 'Unknown', 'directory': str(home)}).status_code == 404
    assert not probes
    result = setup.client.post(base, json={'name': 'Logged out', 'directory': str(home)})
    assert result.status_code == 422
    with Session(setup.engine) as db:
        assert list(db.scalars(select(CodexProfile))) == []
