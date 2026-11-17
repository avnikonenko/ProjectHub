"""Parameters are project-scoped, independently saved and explicitly generated."""
from copy import deepcopy
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, GenerationRevision
from backend.app.integrations.codex import router as routes
from backend.app.integrations.codex.protocol import CodexError


def test_parameter_read_history_generation_and_freshness_are_project_scoped(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    saved = {'generated_at': 'then', 'items': [], 'sources': [], 'fingerprint': 'before',
             'coverage': {'partial': False, 'limitations': []}}
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')])
        db.flush()
        own = GenerationRevision(project_id=1, kind='parameters', generated_at='then', snapshot=saved)
        foreign = GenerationRevision(project_id=2, kind='parameters', generated_at='private',
                                     snapshot={**saved, 'generated_at': 'private'})
        db.add_all([own, foreign]); db.flush()
        own_id, foreign_id = own.id, foreign.id
        db.commit()
    inspection = {'coverage': {'partial': False, 'limitations': []},
                  'diagnostics': {'ai_requests': 0}, 'fingerprint': 'before'}
    monkeypatch.setattr(routes, 'collect_parameter_evidence', lambda *args: deepcopy(inspection))
    def no_model(*args):
        raise AssertionError('Browsing parameters must not invoke Codex')
    monkeypatch.setattr(routes, 'manager_for', no_model)
    app = FastAPI(); app.state.engine = engine; app.include_router(routes.router)
    try:
        with TestClient(app) as client:
            prefix = '/api/projects/1/codex'
            read = client.get(prefix + '/parameters').json()
            assert read['snapshot'] == saved and read['freshness']['state'] == 'unchanged'
            inspection['fingerprint'] = 'after'
            assert client.get(prefix + '/parameters').json()['freshness']['state'] == 'changed'
            inspection['coverage']['freshness_unknown'] = True
            assert client.get(prefix + '/parameters').json()['freshness']['state'] == 'unknown'
            assert client.get('/api/projects/999/codex/parameters').status_code == 404
            page = client.get(prefix + '/revisions?kind=parameters').json()
            assert [row['id'] for row in page['results']] == [own_id]
            assert client.get(prefix + f'/revisions/{foreign_id}').status_code == 404
            assert client.get(prefix + '/generation/status?kind=parameters').status_code == 200
            assert client.post(prefix + '/generation/cancel', json={
                'kind': 'parameters', 'started_at': 'not-running'}).status_code == 409
            assert client.post(prefix + '/parameters', json={}).status_code == 422
            calls = []
            selected = object()
            def choose(model, effort):
                calls.append((model, effort)); return selected
            monkeypatch.setattr(routes, 'manager_for', lambda *args: SimpleNamespace(for_generation=choose))
            def generate(actual_engine, project_id, manager):
                assert actual_engine is engine and project_id == 1 and manager is selected
                return {**saved, 'generated_at': 'new'}
            monkeypatch.setattr(routes, 'refresh_parameters', generate)
            response = client.post(prefix + '/parameters', json={'confirmed': True, 'model': 'selected', 'effort': 'medium'})
            assert response.status_code == 200 and calls == [('selected', 'medium')]
            def fail(*args):
                raise CodexError('Parameter model unavailable')
            monkeypatch.setattr(routes, 'refresh_parameters', fail)
            assert client.post(prefix + '/parameters', json={'confirmed': True, 'model': 'selected'}).status_code == 503
            assert client.get(prefix + '/parameters').json()['snapshot'] == saved
    finally:
        engine.dispose()
