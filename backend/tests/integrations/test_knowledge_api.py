"""Pilot HTTP contracts; no Codex calls and no production data."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import router as routes
from backend.app.models import Project


def test_pilot_is_opt_in_and_legacy_brief_route_remains_default(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='One'))
        db.commit()
    selected = object()
    manager = SimpleNamespace(for_generation=lambda model, effort: selected)
    monkeypatch.setattr(routes, 'manager_for', lambda *args: manager)
    calls = []
    def legacy(*args):
        calls.append('legacy')
        return {'pipeline': 'legacy'}
    def pilot(engine_arg, pid, chosen, *, publish):
        assert engine_arg is engine and pid == 1
        assert chosen is selected
        calls.append(('pilot', publish))
        return {'published': publish}
    monkeypatch.setattr(routes, 'refresh_brief', legacy)
    monkeypatch.setattr(routes, 'generate_knowledge_brief', pilot)
    app = FastAPI()
    app.state.engine = engine
    app.include_router(routes.router)
    with TestClient(app) as client:
        assert client.post('/api/projects/1/codex/brief', json={'confirmed': True}).json()['pipeline'] == 'legacy'
        assert calls == ['legacy']
        assert client.post('/api/projects/1/codex/knowledge/brief', json={}).status_code == 422
        body = {'confirmed': True, 'model': 'mock', 'effort': 'low'}
        assert client.post('/api/projects/1/codex/knowledge/brief', json=body).json() == {'published': False}
        assert client.post('/api/projects/1/codex/knowledge/brief', json={**body, 'publish': True}).json() == {'published': True}
        assert client.post('/api/projects/2/codex/knowledge/brief', json=body).status_code == 404
        assert calls == ['legacy', ('pilot', False), ('pilot', True)]
        assert client.get('/api/projects/1/codex/generation/status?kind=knowledge').status_code == 200
    engine.dispose()


def test_read_overlay_reports_unavailable_evidence_without_rewriting_agreement(tmp_path, monkeypatch):
    from backend.app.models import ProjectKnowledge
    engine, _ = initialize(tmp_path)
    saved = {'publication_id': 'old', 'extraction_version': 'v1', 'source_manifest': {'entries': []},
             'items': [{'id': 'choice', 'statement': 'Keep metadata', 'scope': 'project',
                        'design_status': 'AGREED', 'extraction_version': 'v1', 'source_state': 'valid',
                        'evidence': [{'source_id': 'message', 'source_key': 'document', 'revision': 'old'}]}]}
    with Session(engine) as db:
        db.add(Project(name='One')); db.flush()
        db.add(ProjectKnowledge(project_id=1, snapshot=saved)); db.commit()
    monkeypatch.setattr(routes, 'load_sources', lambda *a, **kw: {
        'sources': [], 'manifest': {'fingerprint': 'new', 'entries': [{'source_key': 'document', 'availability': 'unavailable'}]},
        'coverage': {'limitations': ['Stored source unavailable.']}})
    app = FastAPI(); app.state.engine = engine; app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.get('/api/projects/1/codex/knowledge')
        assert response.status_code == 200
        result = response.json()
        assert result['snapshot']['items'][0]['design_status'] == 'AGREED'
        assert result['snapshot']['items'][0]['source_state'] == 'valid'
        assert result['current_evidence_availability'] == {'choice': 'unavailable'}
        assert client.get('/api/projects/2/codex/knowledge').status_code == 404
    with Session(engine) as db:
        assert db.get(ProjectKnowledge, 1).snapshot == saved
    engine.dispose()
