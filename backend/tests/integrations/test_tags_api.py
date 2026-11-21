"""HTTP contract tests mock generation; no App Server or real AI requests."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.integrations.codex import router as routes
from backend.app.integrations.codex.ai_instructions import ai_instructions


@pytest.fixture
def api(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    monkeypatch.setattr(routes, 'manager_for', lambda *a: pytest.fail('Unexpected manager creation'))
    with TestClient(app) as client:
        pid = client.post('/api/projects', json={'name': 'Tags project'}).json()['id']
        yield SimpleNamespace(client=client, pid=pid, app=app, base=f'/api/projects/{pid}/codex')
    app.state.engine.dispose()


def test_confirmation_ownership_and_exact_target_identity(api):
    body = {'target': 'knowledge', 'publication_id': 'pub'}
    assert api.client.post(api.base + '/tags', json=body).status_code == 422
    assert api.client.post('/api/projects/999/codex/tags', json={**body, 'confirmed': True}).status_code == 404
    for invalid in (
        {'target': 'qa'}, {'target': 'knowledge'}, {'target': 'knowledge_preview'},
        {'target': 'qa', 'generated_at': 'time', 'publication_id': 'pub'},
        {'target': 'knowledge', 'publication_id': 'pub', 'generated_at': 'time'},
        {'target': 'foreign', 'publication_id': 'pub'},
    ):
        assert api.client.post(api.base + '/tags', json={'confirmed': True, **invalid}).status_code == 422


def test_tag_status_does_not_create_manager(api):
    response = api.client.get(api.base + '/generation/status?kind=tags')
    assert response.status_code == 200
    assert response.json()['state'] in ('idle', 'completed', 'failed', 'cancelled')


def test_generation_passes_scoped_manager_options_and_instructions(api, monkeypatch):
    selected = object()
    calls = []

    class AccountManager:
        def for_generation(self, model, effort):
            calls.append(('options', model, effort))
            return selected

    manager = AccountManager()
    def for_project(app, project_id):
        assert app is api.app and project_id == api.pid
        calls.append(('account', project_id))
        return manager
    monkeypatch.setattr(routes, 'manager_for', for_project)
    instructions = 'Use the project tag vocabulary.'
    assert api.client.patch(api.base + '/instructions', json={'instructions': instructions}).status_code == 200

    def generate(engine, project_id, passed_manager, **kwargs):
        assert engine is api.app.state.engine and project_id == api.pid
        assert passed_manager is selected and instructions in ai_instructions()
        assert kwargs == {'target': 'knowledge_preview', 'publication_id': 'pub', 'generated_at': None, 'revision_id': None}
        return {'target': 'knowledge_preview', 'publication_id': 'pub', 'topic_groups': [], 'topic_grouping': {}}
    monkeypatch.setattr(routes, 'generate_tags', generate)
    response = api.client.post(api.base + '/tags', json={
        'confirmed': True, 'target': 'knowledge_preview', 'publication_id': 'pub',
        'model': 'chosen-model', 'effort': 'high'})
    assert response.status_code == 200
    assert response.json()['publication_id'] == 'pub'
    assert calls == [('account', api.pid), ('options', 'chosen-model', 'high')]
    assert instructions not in ai_instructions()


@pytest.mark.parametrize('error', [routes.BriefBusy('busy'), routes.TagGenerationConflict('changed')])
def test_busy_and_changed_results_report_conflict(api, monkeypatch, error):
    monkeypatch.setattr(routes, 'manager_for', lambda *a: object())
    def fail(*a, **k):
        raise error
    monkeypatch.setattr(routes, 'generate_tags', fail)
    response = api.client.post(api.base + '/tags', json={
        'confirmed': True, 'target': 'qa', 'generated_at': 'saved-time'})
    assert response.status_code == 409
    assert response.json()['detail'] == str(error)


def test_tag_cancellation_kind_and_project_scope(api, monkeypatch):
    seen = []
    monkeypatch.setattr(routes.progress, 'cancel', lambda *args: seen.append(args) or {'state': 'running'})
    response = api.client.post(api.base + '/generation/cancel', json={'kind': 'tags', 'started_at': 'run-time'})
    assert response.status_code == 200
    assert seen == [(api.pid, 'tags', 'run-time')]
    assert api.client.post('/api/projects/999/codex/generation/cancel', json={
        'kind': 'tags', 'started_at': 'run-time'}).status_code == 404
    assert api.client.post(api.base + '/generation/cancel', json={
        'kind': 'tags'}).status_code == 422
