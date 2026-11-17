"""Shared analysis HTTP contracts; no Codex calls and no production data."""
from types import SimpleNamespace
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import router as routes
from backend.app.models import Project


def test_preview_is_opt_in_and_legacy_brief_route_publishes_the_shared_pair(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='One'))
        db.commit()
    selected = object()
    manager = SimpleNamespace(for_generation=lambda model, effort: selected)
    monkeypatch.setattr(routes, 'manager_for', lambda *args: manager)
    calls = []
    def legacy(*args):
        pytest.fail('The legacy HTTP route must not independently generate an Overview.')
    def pilot(engine_arg, pid, chosen, *, publish):
        assert engine_arg is engine and pid == 1
        assert chosen is selected or chosen is manager
        calls.append(('pilot', publish))
        return {'published': publish, 'brief': {'pipeline': 'shared'}}
    monkeypatch.setattr(routes, 'refresh_brief', legacy)
    monkeypatch.setattr(routes, 'generate_knowledge_brief', pilot)
    app = FastAPI()
    app.state.engine = engine
    app.include_router(routes.router)
    with TestClient(app) as client:
        assert client.post('/api/projects/1/codex/brief', json={'confirmed': True}).json()['pipeline'] == 'shared'
        assert calls == [('pilot', True)]
        assert client.post('/api/projects/1/codex/knowledge/brief', json={}).status_code == 422
        body = {'confirmed': True, 'model': 'mock', 'effort': 'low'}
        assert client.post('/api/projects/1/codex/knowledge/brief', json=body).json()['published'] is False
        assert client.post('/api/projects/1/codex/knowledge/brief', json={**body, 'publish': True}).json()['published'] is True
        assert client.post('/api/projects/2/codex/knowledge/brief', json=body).status_code == 404
        assert calls == [('pilot', True), ('pilot', False), ('pilot', True)]
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


@pytest.mark.parametrize(('knowledge_publication', 'brief_publication', 'paired'), [
    (None, None, False), ('', '', False), (' ', ' ', False),
    ('one', None, False), (None, 'one', False), ('one', 'two', False),
    ('one', 'one', True),
])
def test_pairing_requires_nonempty_matching_publication_ids(
        tmp_path, monkeypatch, knowledge_publication, brief_publication, paired):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='One'))
        db.commit()
    saved = {'project_id': 1, 'publication_id': knowledge_publication,
             'extraction_version': 'v1', 'source_manifest': {'entries': []}, 'items': []}
    brief = {'project_state': {'publication_id': brief_publication}}
    monkeypatch.setattr(routes, 'read_knowledge', lambda *args: saved)
    monkeypatch.setattr(routes, 'read_brief', lambda *args: brief)
    monkeypatch.setattr(routes, 'load_sources', lambda *args, **kwargs: {
        'sources': [], 'manifest': {'entries': []}, 'coverage': {}})
    app = FastAPI()
    app.state.engine = engine
    app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.get('/api/projects/1/codex/knowledge')
        assert response.status_code == 200
        assert response.json()['paired_with_current_brief'] is paired
        assert response.json()['snapshot'] == saved
    engine.dispose()
