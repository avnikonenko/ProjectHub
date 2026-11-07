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
