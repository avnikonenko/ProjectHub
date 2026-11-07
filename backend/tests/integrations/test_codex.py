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
from backend.app.models import Base, Project, Decision, CodexSession, Delivery, ContextPackage, CodexEvent
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
    monkeypatch.setattr(module, 'availability', lambda: {
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
    monkeypatch.setattr(module, 'availability', lambda: {'installed': False, 'app_server': False,
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
