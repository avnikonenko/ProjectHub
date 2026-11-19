from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project
from backend.app.integrations.codex import accounts, manager
from backend.app.integrations.codex.protocol import CodexError


def test_account_routes_private_client_environment_and_keeps_history_reader(tmp_path, monkeypatch):
    home = tmp_path / 'profile'
    home.mkdir()
    monkeypatch.setattr(accounts, 'account_homes', lambda: {'profile:test': home})
    engine, data = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add_all([Project(name='One', codex_account='profile:test'), Project(name='Two')])
        db.commit()
    root = manager.CodexManager(engine, data)
    app = SimpleNamespace(state=SimpleNamespace(engine=engine, codex_manager=root))
    child = manager.manager_for(app, 1)
    assert child is manager.manager_for(app, 1)
    assert manager.manager_for(app, 2) is root
    assert child.client_factory.keywords == {'codex_home': str(home)}
    assert child.history_client_factory is root.client_factory
    assert child.clients is not root.clients
    assert child.generation_account == str(home)
    with pytest.raises(CodexError, match='unavailable'):
        accounts.account_home('../../other')
    root.close()
    engine.dispose()


def test_discovery_hides_missing_and_duplicate_directories_and_preserves_selected_alias(tmp_path, monkeypatch):
    home = tmp_path / 'launch'; home.mkdir()
    other = tmp_path / 'other'; other.mkdir()
    alias = tmp_path / 'alias'; alias.symlink_to(home, target_is_directory=True)
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setattr(accounts, 'account_homes', lambda: {
        'default': home, 'current': alias, 'profile:launch': home,
        'profile:other': other, 'profile:missing': tmp_path / 'missing',
    })
    assert accounts.account_options() == [{'id': 'profile:other', 'label': 'other'}]
    options = accounts.account_options(selected='profile:launch')
    assert options == [{'id': 'profile:launch', 'label': 'launch'}, {'id': 'profile:other', 'label': 'other'}]


def test_global_profile_registration_works_without_a_project(tmp_path, monkeypatch):
    from importlib import import_module
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from backend.app.main import create_app
    from backend.app.models import CodexProfile

    routes = import_module('backend.app.integrations.codex.router')
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    monkeypatch.setattr(routes, 'availability', lambda *_: {
        'authenticated': True, 'app_server': True, 'error': '',
    })
    directory = tmp_path / 'codex-directory'
    directory.mkdir()
    app = create_app(tmp_path / 'data')
    with TestClient(app) as client:
        assert client.get('/api/projects').json() == []
        result = client.post('/api/settings/codex-profiles',
                             json={'name': 'Work', 'directory': str(directory)})
        assert result.status_code == 200, result.text
        assert result.json()['added'] is True
        assert result.json()['registered_id'].startswith('registered:')
        duplicate = client.post('/api/settings/codex-profiles',
                                json={'name': 'Alias', 'directory': str(directory)})
        assert duplicate.status_code == 409
        assert client.get('/api/projects').json() == []
        with Session(app.state.engine) as db:
            assert len(list(db.scalars(select(CodexProfile)))) == 1
    app.state.engine.dispose()


def test_ai_profiles_register_and_route_only_to_their_provider(tmp_path, monkeypatch):
    from importlib import import_module
    from fastapi.testclient import TestClient
    from backend.app.main import create_app

    routes = import_module('backend.app.integrations.codex.router')
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    monkeypatch.setattr(accounts, 'account_homes', lambda: {})
    monkeypatch.setattr(routes, 'availability', lambda *_: {
        'authenticated': True, 'app_server': True, 'error': '',
    })
    verified = []

    def verify_account(selected):
        verified.append(selected._directory)
        return selected

    monkeypatch.setattr(routes.ClaudeGenerationManager, 'verify_account', verify_account)
    monkeypatch.setattr(routes, 'refresh_scheme', lambda engine, project_id, selected, **kwargs: {
        'account': selected.generation_metadata['account'],
    })
    app = create_app(tmp_path / 'data')
    with TestClient(app) as client:
        profile_ids = {}
        for provider in ('codex', 'claude'):
            directory = tmp_path / provider
            directory.mkdir()
            response = client.post('/api/settings/ai-profiles', json={
                'provider': provider, 'name': f'{provider} work', 'directory': str(directory),
            })
            assert response.status_code == 200, response.text
            profile_ids[provider] = response.json()['registered_id']
        for provider, identifier in profile_ids.items():
            response = client.get('/api/settings/ai-profiles', params={'provider': provider})
            assert response.json()['options'] == [{'id': identifier, 'label': f'{provider} work'}]
        with pytest.raises(CodexError, match='unavailable'):
            accounts.account_home(profile_ids['claude'], app.state.engine)

        project = client.post('/api/projects', json={'name': 'Project'}).json()
        url = f"/api/projects/{project['id']}/codex/scheme"
        request = {'confirmed': True, 'provider': 'claude', 'ai_profile': profile_ids['claude']}
        response = client.post(url, json=request)
        assert response.status_code == 200, response.text
        assert response.json()['account'] == 'claude work'
        assert client.post(url, json={**request, 'ai_profile': profile_ids['codex']}).status_code == 503
        assert client.post(url, json={**request, 'provider': 'codex'}).status_code == 422
        assert verified == [str(tmp_path / 'claude')] * 2


def test_registered_names_win_without_duplicate_options_or_identity_changes(tmp_path, monkeypatch):
    from backend.app.models import CodexProfile

    home = tmp_path / 'launch'; home.mkdir()
    other = tmp_path / 'other'; other.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setattr(accounts, 'account_homes', lambda: {
        'current': home, 'profile:launch': home, 'profile:other': other,
    })
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add_all([CodexProfile(name='Personal', directory=str(home)),
                    CodexProfile(name='Work', directory=str(other))])
        db.commit()
    assert accounts.launch_label(engine) == 'Personal'
    assert accounts.account_options(engine) == [{'id': 'registered:2', 'label': 'Work'}]
    assert accounts.account_options(engine, selected='profile:other') == [
        {'id': 'profile:other', 'label': 'Work'}]
    assert accounts.account_options(engine, selected='profile:launch') == [
        {'id': 'profile:launch', 'label': 'Personal'}, {'id': 'registered:2', 'label': 'Work'}]
    engine.dispose()


@pytest.mark.parametrize('launch', [False, True])
def test_explicit_registration_names_discovered_and_launch_directories(tmp_path, monkeypatch, launch):
    from importlib import import_module
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    from backend.app.models import CodexProfile

    routes = import_module('backend.app.integrations.codex.router')
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    directory = tmp_path / 'profile'; directory.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(directory if launch else tmp_path / 'launch'))
    monkeypatch.setattr(accounts, 'account_homes', lambda: {'profile:automatic': directory})
    monkeypatch.setattr(routes, 'availability', lambda *_: {
        'authenticated': True, 'app_server': True, 'error': '',
    })
    app = create_app(tmp_path / 'data')
    with TestClient(app) as client:
        project = client.post('/api/projects', json={'name': 'Project'}).json()
        response = client.post('/api/settings/codex-profiles',
                               json={'name': 'Named profile', 'directory': str(directory)})
        assert response.status_code == 409, response.text
        with Session(app.state.engine) as db:
            assert db.query(CodexProfile).count() == 0
        alias = tmp_path / 'alias'
        alias.symlink_to(directory, target_is_directory=True)
        assert client.post('/api/settings/codex-profiles',
                           json={'name': 'Alias', 'directory': str(alias)}).status_code == 409
        assert client.get('/api/projects/99999/codex/account').status_code == 404
    app.state.engine.dispose()
