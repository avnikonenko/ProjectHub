"""Agent reading uses the real HTTP/database path without model generation."""
import json

from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_agent_routes_are_read_only_scoped_and_validate_limits(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    app = create_app(tmp_path / 'data')
    try:
        with TestClient(app) as client:
            first = client.post('/api/projects', json={'name': 'Public example'}).json()['id']
            other = client.post('/api/projects', json={'name': 'Other project'}).json()['id']
            note = client.post(f'/api/projects/{first}/notes',
                               json={'title': 'Visible', 'content': 'Own saved context.'}).json()
            foreign = client.post(f'/api/projects/{other}/notes',
                                  json={'title': 'Foreign secret', 'content': 'Do not include me.'}).json()
            base = f'/api/projects/{first}/agent'
            context = client.get(base + '/context')
            assert context.status_code == 200, context.text
            assert 'Foreign secret' not in context.text and 'Do not include me.' not in context.text
            notes = client.get(base + '/read', params={'kind': 'notes'}).json()
            assert notes['project_id'] == first and notes['total'] == 1
            assert 'Own saved context.' in json.dumps(notes)
            hidden = client.get(base + '/read', params={'kind': 'notes', 'record_id': foreign['id']})
            assert hidden.status_code == 200 and hidden.json()['items'] == []
            for params in ({'kind': 'notes', 'limit': 21}, {'kind': 'notes', 'offset': -1},
                           {'kind': 'notes', 'text_offset': -1}, {'kind': 'raw_events'}):
                assert client.get(base + '/read', params=params).status_code == 422
            assert client.get('/api/projects/999999/agent/context').status_code == 404
            assert client.get('/api/projects/999999/agent/read?kind=notes').status_code == 404
            assert client.get(f"/api/projects/{first}/notes/{note['id']}").json()['content'] == 'Own saved context.'
    finally:
        app.state.engine.dispose()
