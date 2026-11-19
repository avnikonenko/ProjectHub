from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.integrations.codex.knowledge_profiles import current_knowledge_profile


routes = import_module('backend.app.integrations.codex.router')


class ModelSelection:
    def __init__(self, model=None, effort=None):
        self.generation_model = model
        self.generation_effort = effort

    def for_generation(self, model, effort):
        return ModelSelection(model, effort)

    def generation_models(self):
        return [{'id': 'model', 'efforts': ['low', 'high']}]


@pytest.mark.parametrize('route,refresh,input_type', [
    ('generate_brief', 'refresh_brief', routes.GenerationInput),
    ('generate_qa', 'refresh_qa', routes.QAGenerationInput),
    ('generate_scheme', 'refresh_scheme', routes.SchemeGenerationInput),
    ('generate_parameters', 'refresh_parameters', routes.GenerationInput),
])
@pytest.mark.parametrize('profile,model,explicit,expected', [
    ('economy', 'model', None, 'low'),
    ('economy', 'model', 'high', 'high'),
    ('economy', None, None, None),
    ('balanced', 'model', None, None),
    ('quality', 'model', None, None),
])
def test_review_routes_capture_profile_and_preserve_explicit_choices(
        monkeypatch, route, refresh, input_type, profile, model, explicit, expected):
    monkeypatch.setattr(routes, 'ai_worker_settings', lambda: {'generation_profile': profile})
    monkeypatch.setattr(routes, 'project', Mock())
    monkeypatch.setattr(routes, 'manager_for', lambda *_: ModelSelection())
    monkeypatch.setattr(routes, 'read_project_instructions', lambda *_: '')
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=object())))
    database = Mock()

    def generate(engine, project_id, manager, **options):
        assert engine is request.app.state.engine and project_id == 3
        assert database.rollback.called
        assert current_knowledge_profile().id == profile
        assert manager.generation_model == model
        assert manager.generation_effort == expected
        return {'saved': True}

    monkeypatch.setattr(routes, refresh, generate)
    body = input_type(confirmed=True, model=model, effort=explicit)
    assert getattr(routes, route)(3, body, request, database) == {'saved': True}


def test_claude_selection_is_opt_in_and_keeps_provider_cache_keys_separate():
    from backend.app.integrations.codex.generation_attempts import settings_for
    from backend.app.integrations.codex.knowledge import extraction_configuration
    from backend.app.integrations.codex.staged_overview import _requested_provenance

    codex = ModelSelection('sonnet', 'high')
    request = routes.GenerationInput(confirmed=True, provider='claude', model='sonnet', effort='high')
    claude = routes.select_generation_manager(codex, request)
    assert claude.generation_provider == 'claude'
    assert settings_for(claude, None)['provider'] == 'claude'
    assert _requested_provenance(claude)['provider'] == 'claude'
    assert extraction_configuration(claude)['provider'] == 'claude'
    assert 'provider' not in extraction_configuration(codex)
    assert routes.GenerationInput().provider == 'codex'


def test_claude_http_generation_and_model_catalog_do_not_resolve_codex_account(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from backend.app.db import initialize
    from backend.app.models import Project

    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='Provider selection'))
        db.commit()
    app = FastAPI()
    app.state.engine = engine
    app.include_router(routes.router)
    calls = []

    def root_history_manager(*args):
        assert args == (app,)
        return object()

    def refresh(engine_arg, project_id, manager, *, focus):
        assert engine_arg is engine and project_id == 1
        calls.append((manager.generation_provider, manager.generation_model, focus))
        return {'saved': True, 'generation': manager.generation_metadata}

    monkeypatch.setattr(routes, 'manager_for', root_history_manager)
    monkeypatch.setattr(routes, 'refresh_scheme', refresh)
    with TestClient(app) as client:
        models = client.get('/api/projects/1/codex/models?provider=claude')
        assert models.status_code == 200
        assert {item['id'] for item in models.json()} == {'sonnet', 'opus', 'haiku'}
        path = '/api/projects/1/codex/scheme'
        response = client.post(path, json={'confirmed': True, 'provider': 'claude', 'model': 'sonnet', 'focus': 'code'})
        assert response.status_code == 200
        assert response.json()['generation']['provider'] == 'claude'
        assert client.post(path, json={'confirmed': False, 'provider': 'claude'}).status_code == 422
        assert client.post(path, json={'confirmed': True, 'provider': 'unknown'}).status_code == 422
        assert calls == [('claude', 'sonnet', 'code')]
    engine.dispose()
