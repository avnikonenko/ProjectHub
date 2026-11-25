"""HTTP contract checks with mocked Codex; no external AI requests."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import AuditEvent, GenerationRevision, Project, ProjectScheme
from backend.app.integrations.codex import router as routes
from backend.app.integrations.codex.protocol import CodexError
from backend.tests.integrations.test_scheme_evidence import repository, setup


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


@pytest.mark.parametrize('legacy', [False, True])
def test_scheme_dependency_purposes_follow_the_linked_repository(tmp_path, legacy):
    old = repository(tmp_path)
    second_root = tmp_path / 'second'
    second_root.mkdir()
    new = repository(second_root)
    for repo in (old, new):
        (repo / 'environment.yml').write_text('dependencies:\n  - bubblewrap\n')
    engine = setup(tmp_path, old)
    evidence = routes.collect_evidence(engine, 1)
    inventory = deepcopy(evidence['dependency_inventory'])
    if legacy:
        inventory.pop('repository_identity')
    inventory['dependencies'][0]['purpose'] = 'Runs the first repository’s isolated worker.'
    snapshot = {'generated_at': '2026-01-02T10:00:00+00:00', 'fingerprint': evidence['fingerprint'],
                'dependency_inventory': inventory}
    with Session(engine) as db:
        db.add(ProjectScheme(project_id=1, snapshot=snapshot))
        if legacy:
            db.add(AuditEvent(project_id=1, entity_type='projects', entity_id=1, action='created',
                              created_at='2026-01-01T08:00:00+00:00', after={'repository_path': str(old)}))
            db.add(AuditEvent(project_id=1, entity_type='projects', entity_id=1, action='updated',
                              created_at='2026-01-03T09:00:00+00:00', before={'repository_path': str(old)},
                              after={'repository_path': str(old), 'description': 'A metadata-only update'}))
        db.commit()

    app = FastAPI()
    app.state.engine = engine
    app.include_router(routes.router)
    try:
        with TestClient(app) as client:
            path = '/api/projects/1/codex/scheme'
            first = client.get(path).json()
            first_inventory = first['inspection']['dependency_inventory']
            assert first_inventory['dependencies'][0]['purpose'] == inventory['dependencies'][0]['purpose']
            if legacy:
                assert first['inspection']['saved_dependency_repository_matches'] is True
                assert 'repository_identity' not in first['snapshot']['dependency_inventory']
            with Session(engine) as db:
                db.get(Project, 1).repository_path = str(new)
                db.add(AuditEvent(project_id=1, entity_type='projects', entity_id=1, action='updated',
                                  created_at='2026-01-03T10:00:00+00:00', before={'repository_path': str(old)},
                                  after={'repository_path': str(new)}))
                db.commit()
            assert routes.collect_evidence(engine, 1)['fingerprint'] == evidence['fingerprint']
            second = client.get(path).json()
            current = second['inspection']['dependency_inventory']
            assert current['repository_identity'] != first_inventory['repository_identity']
            assert current['dependencies'][0]['id'] == first_inventory['dependencies'][0]['id']
            assert current['dependencies'][0]['purpose'] == ''
            assert second['snapshot'] == snapshot
            if legacy:
                assert second['inspection']['saved_dependency_repository_matches'] is False
                with Session(engine) as db:
                    db.get(Project, 1).repository_path = str(old)
                    db.add(AuditEvent(project_id=1, entity_type='projects', entity_id=1, action='updated',
                                      created_at='2026-01-03T11:00:00+00:00', before={'repository_path': str(new)},
                                      after={'repository_path': str(old)}))
                    db.commit()
                returned = client.get(path).json()['inspection']
                assert returned['saved_dependency_repository_matches'] is False
                assert returned['dependency_inventory']['dependencies'][0]['purpose'] == ''
                # A snapshot timestamped after the relink still cannot prove
                # ownership if its generation began before that relink.
                with Session(engine) as db:
                    db.get(ProjectScheme, 1).snapshot = {
                        **snapshot, 'generated_at': '2026-01-03T11:01:00+00:00',
                        'diagnostics': {'duration_seconds': 120},
                    }
                    db.commit()
                during_generation = client.get(path).json()['inspection']
                assert during_generation['saved_dependency_repository_matches'] is False
                assert during_generation['dependency_inventory']['dependencies'][0]['purpose'] == ''
    finally:
        engine.dispose()
