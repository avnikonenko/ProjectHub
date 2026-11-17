"""Independent Overview and Knowledge HTTP contracts; mocked generation only."""
from copy import deepcopy
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, ProjectAIInstructions
from backend.app.integrations.codex import progress
from backend.app.integrations.codex import router as routes
from backend.app.integrations.codex.ai_instructions import ai_instructions
from backend.app.integrations.codex.knowledge_previews import KnowledgePreviewConflict


def result(project_id, *, published=False):
    publication = f'saved-{project_id}'
    return {'published': published, 'publication_id': publication,
            'knowledge': {'project_id': project_id, 'publication_id': publication, 'items': []},
            'brief': {'generated_at': 'saved', 'sections': {}, 'sources': [], 'coverage': [],
                      'project_state': {'publication_id': publication}},
            'baseline_brief': None, 'diagnostics': {}}


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')]); db.flush()
        db.add_all([ProjectAIInstructions(project_id=pid, instructions=f'Only project {pid} preferences.',
                                         replaces_shared=True) for pid in (1, 2)])
        db.commit()
    calls = []
    def selected(model, effort):
        calls.append(('selection', model, effort))
        return chosen
    manager, chosen = SimpleNamespace(for_generation=selected), object()
    def manager_for(app, pid):
        calls.append(('manager', pid))
        return manager
    def generate(engine_arg, pid, picked, *, publish):
        assert engine_arg is engine
        assert picked in (manager, chosen)
        assert f'Only project {pid} preferences.' in ai_instructions()
        calls.append(('generate', pid, picked is chosen, publish))
        return result(pid, published=publish)
    def read(db, pid):
        calls.append(('read', pid))
        return result(pid)
    def overview(engine_arg, pid, picked):
        assert engine_arg is engine
        assert f'Only project {pid} preferences.' in ai_instructions()
        calls.append(('overview', pid, picked is chosen))
        return {'generated_at': 'general', 'sections': {'overview': []}}
    def activate(engine_arg, pid, publication):
        assert engine_arg is engine
        calls.append(('publish', pid, publication))
        if publication != f'saved-{pid}':
            raise KnowledgePreviewConflict('Selected result belongs to another project or is unavailable.')
        return result(pid, published=True)
    monkeypatch.setattr(routes, 'manager_for', manager_for)
    monkeypatch.setattr(routes, 'generate_knowledge_brief', generate)
    monkeypatch.setattr(routes, 'refresh_brief', overview)
    monkeypatch.setattr(routes, 'read_knowledge_preview', read)
    monkeypatch.setattr(routes, 'publish_saved_knowledge_preview', activate)
    app = FastAPI(); app.state.engine = engine; app.include_router(routes.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, calls=calls, engine=engine)
    engine.dispose()


def test_canonical_preview_requires_confirmation_and_cannot_publish(api):
    client, calls = api.client, api.calls
    path = '/api/projects/1/codex/analysis/preview'
    assert client.post(path, json={}).status_code == 422
    assert client.post(path, json={'confirmed': False}).status_code == 422
    assert client.post(path, json={'confirmed': True, 'publish': True}).status_code == 422
    assert client.post(path, json={'confirmed': True, 'publish': False}).status_code == 422
    assert calls == []
    response = client.post(path, json={'confirmed': True, 'model': 'selected-model', 'effort': 'high'})
    assert response.status_code == 200, response.text
    assert response.json() == result(1)
    assert calls == [('manager', 1), ('selection', 'selected-model', 'high'), ('generate', 1, True, False)]
    assert 'Only project 1 preferences.' not in ai_instructions()


def test_brief_post_generates_only_general_overview(api):
    client = api.client
    path = '/api/projects/1/codex/brief'
    assert client.post(path, json={}).status_code == 422
    assert api.calls == []
    response = client.post(path, json={'confirmed': True})
    assert response.status_code == 200, response.text
    assert response.json() == {'generated_at': 'general', 'sections': {'overview': []}}
    assert ('selection', None, None) in api.calls
    assert ('overview', 1, True) in api.calls
    assert all(call[0] != 'generate' for call in api.calls)
    assert 'knowledge' not in response.json() and 'published' not in response.json()


def test_read_publish_aliases_are_project_scoped_and_do_not_generate(api):
    client = api.client
    for pid in (1, 2):
        prefix = f'/api/projects/{pid}/codex'
        assert client.get(prefix + '/analysis/preview').json() == result(pid)
        assert client.get(prefix + '/knowledge/preview').json() == result(pid)
        for path in ('/analysis/publish', '/knowledge/preview/publish'):
            response = client.post(prefix + path, json={'publication_id': f'saved-{pid}'})
            assert response.status_code == 200, response.text
            assert response.json() == result(pid, published=True)
    foreign = client.post('/api/projects/2/codex/analysis/publish', json={'publication_id': 'saved-1'})
    assert foreign.status_code == 409
    before = deepcopy(api.calls)
    for method, path, body in [
        ('get', '/analysis/preview', None), ('post', '/analysis/preview', {'confirmed': True}),
        ('post', '/analysis/publish', {'publication_id': 'saved-1'}),
        ('post', '/brief', {'confirmed': True}),
    ]:
        response = getattr(client, method)('/api/projects/999/codex' + path, **({'json': body} if body else {}))
        assert response.status_code == 404
    assert api.calls == before
    assert all(call[0] not in ('generate', 'manager') for call in api.calls)


@pytest.mark.parametrize(('legacy', 'shared', 'expected'), [
    ({'state': 'idle'}, {'state': 'running', 'started_at': '2026-11-17T11:00:00+00:00'}, 'shared'),
    ({'state': 'completed', 'started_at': '2026-11-17T10:00:00+00:00'},
     {'state': 'running', 'started_at': '2026-11-17T11:00:00+00:00'}, 'shared'),
    ({'state': 'completed', 'started_at': '2026-11-17T12:00:00+00:00'},
     {'state': 'failed', 'started_at': '2026-11-17T11:00:00+00:00'}, 'legacy'),
    ({'state': 'completed', 'started_at': '2026-11-17T12:00:00+00:00'}, {'state': 'idle'}, 'legacy'),
])
def test_overview_progress_never_substitutes_knowledge_run(api, monkeypatch, legacy, shared, expected):
    snapshots = {'brief': legacy, 'knowledge': shared}
    monkeypatch.setattr(progress, 'read', lambda pid, kind, **kwargs: deepcopy(snapshots[kind]))
    response = api.client.get('/api/projects/1/codex/generation/status?kind=brief')
    assert response.status_code == 200
    assert response.json() == {**legacy, 'latest_attempt': None}
    assert api.client.get('/api/projects/1/codex/generation/status?kind=knowledge').json() == {**shared, 'latest_attempt': None}


def test_overview_cancel_never_targets_knowledge_run(api, monkeypatch):
    calls = []
    shared = {'state': 'running', 'started_at': 'shared-start'}
    legacy = {'state': 'failed', 'started_at': 'legacy-start'}
    monkeypatch.setattr(progress, 'read', lambda pid, kind, **kwargs: deepcopy(shared if kind == 'knowledge' else legacy))
    def cancel(pid, kind, started_at):
        calls.append((pid, kind, started_at))
        if kind != 'knowledge' or started_at != 'shared-start':
            raise routes.CodexError('This AI generation is no longer running.')
        return {'state': 'running', 'cancel_requested': True}
    monkeypatch.setattr(progress, 'cancel', cancel)
    prefix = '/api/projects/1/codex/generation/cancel'
    assert api.client.post(prefix, json={'kind': 'brief', 'started_at': 'shared-start'}).status_code == 409
    assert calls[-1] == (1, 'brief', 'shared-start')
    assert api.client.post(prefix, json={'kind': 'knowledge', 'started_at': 'shared-start'}).status_code == 200
    assert calls[-1] == (1, 'knowledge', 'shared-start')
    assert api.client.post(prefix, json={'kind': 'brief', 'started_at': 'legacy-start'}).status_code == 409
    assert calls[-1] == (1, 'brief', 'legacy-start')
    assert api.client.post(prefix, json={'kind': 'brief', 'started_at': 'stale'}).status_code == 409
    assert calls[-1] == (1, 'brief', 'stale')


def test_legacy_get_and_revision_history_remain_read_only_and_project_scoped(api):
    from backend.app.models import ProjectBrief, GenerationRevision
    old = {'generated_at': 'historical', 'sections': {'overview': []},
           'sources': [], 'coverage': ['Historical evidence only.'], 'project_map': None, 'generation': None}
    with Session(api.engine) as db:
        db.add(ProjectBrief(project_id=1, **old))
        own = GenerationRevision(project_id=1, kind='brief', generated_at='historical', snapshot=old)
        foreign = GenerationRevision(project_id=2, kind='brief', generated_at='private',
                                     snapshot={**old, 'generated_at': 'private'})
        db.add_all([own, foreign]); db.flush()
        own_id, foreign_id = own.id, foreign.id
        db.commit()
    prefix = '/api/projects/1/codex'
    assert api.client.get(prefix + '/brief').json() == old
    listing = api.client.get(prefix + '/revisions?kind=brief').json()
    assert [row['id'] for row in listing['results']] == [own_id]
    assert api.client.get(prefix + f'/revisions/{own_id}').json() == {'kind': 'brief', 'snapshot': old}
    assert api.client.get(prefix + f'/revisions/{foreign_id}').status_code == 404
    assert api.calls == []


def test_real_cancel_guard_keeps_project_and_generation_kind_independent(api):
    progress.begin(1, 'knowledge')
    try:
        shared = api.client.get('/api/projects/1/codex/generation/status?kind=knowledge').json()
        legacy = api.client.get('/api/projects/1/codex/generation/status?kind=brief').json()
        assert shared['state'] == 'running'
        assert legacy['state'] != 'running'
        body = {'kind': 'brief', 'started_at': shared['started_at']}
        assert api.client.post('/api/projects/2/codex/generation/cancel', json=body).status_code == 409
        assert api.client.post('/api/projects/1/codex/generation/cancel',
                               json={**body, 'started_at': 'stale'}).status_code == 409
        assert not progress.current_cancel_event().is_set()
        assert api.client.post('/api/projects/1/codex/generation/cancel', json=body).status_code == 409
        assert not progress.current_cancel_event().is_set()
        body['kind'] = 'knowledge'
        response = api.client.post('/api/projects/1/codex/generation/cancel', json=body)
        assert response.status_code == 200 and response.json()['cancel_requested'] is True
        assert progress.current_cancel_event().is_set()
    finally:
        progress.finish(1, progress.GenerationCancelled())
    assert api.client.post('/api/projects/1/codex/generation/cancel', json=body).status_code == 409
