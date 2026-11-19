"""HTTP contract checks with mocked Codex; no external AI requests."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import GenerationRevision, Project
from backend.app.integrations.codex import router as routes
from backend.app.integrations.codex.protocol import CodexError


def test_scheme_routes_are_scoped_and_get_never_calls_codex(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(name='One'), Project(name='Two')])
        db.flush()
        db.add(GenerationRevision(project_id=1, kind='scheme', generated_at='2026-01-01', snapshot={'generated_at': '2026-01-01'}))
        db.commit()
    evidence = {
        'sources': [], 'fingerprint': 'abc', 'tree': [], 'dependencies': [],
        'coverage': {'partial': True, 'limitations': ['Discussion only.']},
    }
    monkeypatch.setattr(routes, 'collect_evidence', lambda *args: evidence)
    def no_codex(*args, **kwargs):
        raise AssertionError('Opening Scheme must not contact Codex')
    monkeypatch.setattr(routes, 'manager_for', no_codex)
    app = FastAPI()
    app.state.engine = engine
    app.include_router(routes.router)
    with TestClient(app) as client:
        result = client.get('/api/projects/1/codex/scheme')
        assert result.status_code == 200
        assert result.json()['snapshot'] is None
        assert result.json()['freshness'] == {'state': 'not_generated', 'partial': True, 'limitations': ['Discussion only.']}
        assert client.get('/api/projects/999/codex/scheme').status_code == 404
        assert client.post('/api/projects/1/codex/scheme', json={}).status_code == 422
        assert client.get('/api/projects/1/codex/generation/status?kind=scheme').status_code == 200
        revisions = client.get('/api/projects/1/codex/revisions?kind=scheme').json()['results']
        assert len(revisions) == 1
        assert client.get(f"/api/projects/2/codex/revisions/{revisions[0]['id']}").status_code == 404

        calls = []
        selected = object()
        def choose(model, effort):
            calls.append((model, effort))
            return selected
        monkeypatch.setattr(routes, 'manager_for', lambda *args: SimpleNamespace(for_generation=choose))
        def generate(engine_arg, project_id, manager, *, focus):
            assert engine_arg is engine and project_id == 1 and manager is selected
            return {'generated_at': '2026-01-02', 'focus': focus}
        monkeypatch.setattr(routes, 'refresh_scheme', generate)
        response = client.post('/api/projects/1/codex/scheme', json={'confirmed': True, 'model': 'chosen', 'effort': 'high'})
        assert response.status_code == 200 and calls == [('chosen', 'high')]
        assert response.json()['focus'] == 'balanced'
        response = client.post('/api/projects/1/codex/scheme', json={'confirmed': True, 'focus': 'sessions'})
        assert response.status_code == 200 and response.json()['focus'] == 'sessions'
        assert client.post('/api/projects/1/codex/scheme', json={'confirmed': True, 'focus': 'invalid'}).status_code == 422

        def fail(*args, **kwargs):
            raise CodexError('Codex unavailable')
        monkeypatch.setattr(routes, 'refresh_scheme', fail)
        assert client.post('/api/projects/1/codex/scheme', json={'confirmed': True, 'model': 'chosen'}).status_code == 503
    engine.dispose()
