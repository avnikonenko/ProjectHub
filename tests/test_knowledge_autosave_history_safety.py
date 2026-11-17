"""Legacy saved output remains history; reading never activates stale evidence."""
from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import CodexSession, GenerationRevision, Project, ProjectBrief, ProjectKnowledge
from backend.app.integrations.codex import router as routes


def test_legacy_history_with_a_moved_source_is_read_only_and_project_scoped(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    timestamp = '2026-11-17T10:00:00+00:00'
    publication = 'historical-version'
    quotation = 'Consider an upstream preparation step.'
    candidate = {
        'project_id': 1, 'publication_id': publication, 'generated_at': timestamp,
        'items': [{'id': 'earlier-choice', 'statement': quotation, 'category': 'PROPOSAL',
                   'design_status': 'PROPOSED', 'scope': 'upstream preparation',
                   'source_state': 'stale', 'evidence': [
                       {'source_id': 'session:17:turn:old', 'role': 'user', 'excerpt': quotation}]}],
        'sources': [{'id': 'session:17:turn:old', 'session_id': 17, 'text': quotation,
                     'label': 'Earlier linked discussion'}],
    }
    brief = {'generated_at': timestamp, 'project_state': {'publication_id': publication},
             'sections': {}, 'sources': [], 'coverage': []}
    archived = {'published': False, 'publication_id': publication, 'knowledge': candidate,
                'brief': brief, 'baseline_brief': None, 'diagnostics': {}}
    try:
        with Session(engine) as db:
            db.add_all([Project(id=1, name='Original project'), Project(id=2, name='Destination')])
            db.flush()
            db.add(CodexSession(id=17, project_id=2, thread_id='moved-external-thread', status='linked'))
            db.add(ProjectBrief(project_id=1, generated_at='existing-overview', sections={}, sources=[], coverage=[]))
            db.add(GenerationRevision(project_id=1, kind='knowledge_preview', generated_at=timestamp,
                                      snapshot={'generated_at': timestamp, 'result': deepcopy(archived)}))
            db.commit()
        def unexpected_write(*args, **kwargs):
            pytest.fail('Reading legacy history attempted generation or activation')
        monkeypatch.setattr(routes, 'generate_knowledge_brief', unexpected_write)
        monkeypatch.setattr(routes, 'publish_saved_knowledge_preview', unexpected_write)
        monkeypatch.setattr(routes.progress, 'read', lambda *args, **kwargs: {'state': 'idle'})
        app = FastAPI()
        app.state.engine = engine
        app.include_router(routes.router)
        with TestClient(app) as client:
            summary = client.get('/api/projects/1/codex/generation/summary')
            assert summary.status_code == 200, summary.text
            knowledge = next(view for view in summary.json()['views'] if view['kind'] == 'knowledge')
            assert knowledge['state'] == 'earlier_result'
            assert knowledge['saved_at'] is None and knowledge['history_at'] == timestamp
            history = client.get('/api/projects/1/codex/analysis/preview')
            assert history.status_code == 200 and history.json() == archived
            current = client.get('/api/projects/1/codex/knowledge')
            assert current.status_code == 200 and current.json()['snapshot'] is None
            assert client.get('/api/projects/2/codex/analysis/preview').json() is None
            other = client.get('/api/projects/2/codex/generation/summary').json()
            foreign = next(view for view in other['views'] if view['kind'] == 'knowledge')
            assert foreign['state'] == 'not_generated' and foreign['history_at'] is None
        with Session(engine) as db:
            assert db.get(ProjectKnowledge, 1) is None and db.get(ProjectKnowledge, 2) is None
            assert db.get(ProjectBrief, 1).generated_at == 'existing-overview'
            assert db.get(CodexSession, 17).project_id == 2
            assert db.scalar(select(func.count()).select_from(GenerationRevision)) == 1
            assert db.scalar(select(GenerationRevision.snapshot)) == {
                'generated_at': timestamp, 'result': archived}
    finally:
        engine.dispose()
