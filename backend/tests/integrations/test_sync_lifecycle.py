from fastapi.testclient import TestClient

from backend.app.integrations.codex import history_sync
from backend.app.main import create_app


def test_sync_worker_lifecycle_and_project_scoped_status(tmp_path, monkeypatch):
    calls = []

    class FakeSync:
        def __init__(self, engine, manager_factory, interval):
            assert interval == 90
            self.manager_factory = manager_factory

        def start(self):
            calls.append('start')

        def close(self):
            calls.append('close')

        def read_status(self, project_id):
            return {'error': f'Could not read project {project_id} history.'}

    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'true')
    monkeypatch.setenv('PROJECTHUB_HISTORY_SYNC_INTERVAL', '90')
    monkeypatch.setattr(history_sync, 'HistorySync', FakeSync)
    app = create_app(tmp_path / 'data')
    with TestClient(app) as client:
        assert calls == ['start']
        pid = client.post('/api/projects', json={'name': 'Example'}).json()['id']
        result = client.get(f'/api/projects/{pid}/codex/history/sync')
        assert result.json() == {'enabled': True, 'error': f'Could not read project {pid} history.',
                                 'indexed_at': None, 'indexed_turns': 0}
        assert client.get('/api/projects/99999/codex/history/sync').status_code == 404
    assert calls == ['start', 'close']


def test_automatic_sync_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    app = create_app(tmp_path / 'data')
    with TestClient(app) as client:
        assert app.state.history_sync is None
        pid = client.post('/api/projects', json={'name': 'Example'}).json()['id']
        assert client.get(f'/api/projects/{pid}/codex/history/sync').json() == {
            'enabled': False, 'error': None,
            'indexed_at': None, 'indexed_turns': 0,
        }
