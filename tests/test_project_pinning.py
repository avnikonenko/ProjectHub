"""Pinned ordering uses real SQLite, migrations and HTTP; no model calls."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from backend.app.main import create_app


def test_project_pins_order_and_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    data = tmp_path / 'data'
    app = create_app(data)
    with TestClient(app) as client:
        first = client.post('/api/projects', json={'name': 'First'}).json()
        second = client.post('/api/projects', json={'name': 'Second'}).json()
        third = client.post('/api/projects', json={'name': 'Third', 'pinned': True}).json()
        assert first['pinned'] is False and third['pinned'] is True
        for suffix in ('', '?compact=true'):
            assert [p['id'] for p in client.get('/api/projects' + suffix).json()] == [third['id'], first['id'], second['id']]
        assert client.patch(f"/api/projects/{second['id']}", json={'pinned': True}).json()['pinned'] is True
        assert [p['id'] for p in client.get('/api/projects?compact=true').json()] == [second['id'], third['id'], first['id']]
        assert client.patch(f"/api/projects/{third['id']}", json={'pinned': False}).status_code == 200
        assert client.get(f"/api/projects/{third['id']}").json()['name'] == 'Third'
        assert client.patch('/api/projects/999999', json={'pinned': True}).status_code == 404
        for invalid in ('true', 1, None):
            assert client.patch(f"/api/projects/{first['id']}", json={'pinned': invalid}).status_code == 422
            assert client.post('/api/projects', json={'name': 'Invalid', 'pinned': invalid}).status_code == 422
    app.state.engine.dispose()
    restarted = create_app(data)
    with TestClient(restarted) as client:
        projects = client.get('/api/projects?compact=true').json()
        assert projects == [
            {'id': second['id'], 'name': 'Second', 'pinned': True},
            {'id': first['id'], 'name': 'First', 'pinned': False},
            {'id': third['id'], 'name': 'Third', 'pinned': False},
        ]
    restarted.state.engine.dispose()


def test_project_pin_migration_preserves_existing_projects(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'old.sqlite3'))
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).resolve().parents[1] / 'migrations'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, '0020')
        connection.execute(text("INSERT INTO projects (id,name,description,goals,status,git_remote,tags,architecture_mermaid,created_at,updated_at) VALUES (1,'Existing','Keep this','Goals','active','','[]','','then','then')"))
        command.upgrade(config, 'head')
        assert tuple(connection.execute(text('SELECT name,description,goals,status,pinned FROM projects')).one()) == ('Existing', 'Keep this', 'Goals', 'active', 0)
        command.downgrade(config, '0020')
        assert tuple(connection.execute(text('SELECT name,description FROM projects')).one()) == ('Existing', 'Keep this')
    engine.dispose()
