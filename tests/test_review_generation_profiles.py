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
    ('generate_scheme', 'refresh_scheme', routes.GenerationInput),
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
