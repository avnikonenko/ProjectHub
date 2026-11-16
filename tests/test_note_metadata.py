"""Real SQLite and HTTP regressions for on-demand note retrieval."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.app.models import Note, Project
from backend.app.integrations.codex.manager import CodexManager


@pytest.fixture
def note_app(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    monkeypatch.setattr(CodexManager, '_new_client', lambda *a, **k: pytest.fail('Notes invoked Codex'))
    app = create_app(tmp_path / 'data')
    with Session(app.state.engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')])
        db.flush()
        db.add_all([
            Note(id=1, project_id=1, title='Original transcript', content='ligand evidence ' * 10000,
                 kind='document', reference='https://chatgpt.com/share/11111111-1111-1111-1111-111111111111'),
            Note(id=2, project_id=2, title='Other project', content='private foreign note'),
        ])
        db.commit()
    with TestClient(app) as client:
        yield client, app.state.engine
    app.state.engine.dispose()


@pytest.mark.parametrize('compact', [False, True])
def test_note_metadata_is_opt_in_and_preserves_default_contract(note_app, compact):
    client, _ = note_app
    full = client.get('/api/projects/1/overview', params={'compact': compact}).json()
    metadata = client.get('/api/projects/1/overview', params={'compact': compact, 'note_content': False}).json()

    assert full['notes'][0]['content'] == 'ligand evidence ' * 10000
    assert 'content_loaded' not in full['notes'][0]
    assert metadata['notes'] == [{
        **{key: value for key, value in full['notes'][0].items() if key != 'content'},
        'content_loaded': False,
    }]
    assert {key: value for key, value in metadata.items() if key != 'notes'} == {
        key: value for key, value in full.items() if key != 'notes'}


def test_metadata_sql_does_not_fetch_note_content(note_app):
    client, engine = note_app
    statements = []

    def capture(connection, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(engine, 'before_cursor_execute', capture)
    try:
        response = client.get('/api/projects/1/overview?compact=true&note_content=false')
    finally:
        event.remove(engine, 'before_cursor_execute', capture)

    assert response.status_code == 200
    note_reads = [sql.lower() for sql in statements if 'from notes' in sql.lower()]
    assert note_reads
    assert all('notes.content' not in sql and 'select *' not in sql for sql in note_reads)
    activity_reads = [sql.lower().replace('"', '') for sql in statements if 'from audit_events' in sql.lower()]
    assert activity_reads
    assert all('audit_events.before' not in sql and 'audit_events.after' not in sql for sql in activity_reads)


def test_metadata_activity_cannot_reintroduce_full_transcripts(note_app):
    client, _ = note_app
    content = ('large imported transcript payload ' * 2000).strip()
    created = client.post('/api/projects/1/notes', json={'title': 'Large', 'content': content})
    assert created.status_code == 201
    full = client.get('/api/projects/1/overview?compact=true').json()
    metadata = client.get('/api/projects/1/overview?compact=true&note_content=false')
    activity = metadata.json()['activity']

    assert full['activity'][0]['after']['content'] == content
    assert activity[0]['details_loaded'] is False
    assert activity[0]['entity_id'] == created.json()['id']
    assert all('before' not in row and 'after' not in row for row in activity)
    assert 'large imported transcript payload' not in metadata.text
    assert client.get('/api/projects/1/export').json()['audit'][0]['after']['content'] == content


def test_single_note_is_full_and_project_scoped(note_app):
    client, _ = note_app
    note = client.get('/api/projects/1/notes/1')
    assert note.status_code == 200
    assert note.json()['content'] == 'ligand evidence ' * 10000
    assert note.json()['project_id'] == 1
    assert client.get('/api/projects/2/notes/1').status_code == 404
    assert client.get('/api/projects/1/notes/2').status_code == 404
    assert client.get('/api/projects/999/notes/1').status_code == 404
    assert client.get('/api/projects/1/notes/999').status_code == 404


def test_note_list_export_search_and_edit_keep_full_content(note_app):
    client, _ = note_app
    assert client.get('/api/projects/1/notes').json()[0]['content'] == 'ligand evidence ' * 10000
    assert client.get('/api/projects/1/export').json()['notes'][0]['content'] == 'ligand evidence ' * 10000
    groups = client.get('/api/projects/1/search/all?q=ligand').json()['groups']
    assert any(group['results'] for group in groups if group['kind'] == 'conversations')
    updated = client.patch('/api/projects/1/notes/1', json={'content': 'updated original'}).json()
    assert updated['content'] == 'updated original'
    assert client.get('/api/projects/1/notes/1').json()['content'] == 'updated original'
