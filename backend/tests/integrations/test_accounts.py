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
        assert duplicate.json()['added'] is False
        assert client.get('/api/projects').json() == []
        with Session(app.state.engine) as db:
            assert len(list(db.scalars(select(CodexProfile)))) == 1
    app.state.engine.dispose()
