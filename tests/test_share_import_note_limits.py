"""Import validation and non-destructive handling of existing oversized notes."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.main import create_app
from backend.app.models import AuditEvent, Note, Project
from backend.app.schemas import NOTE_CONTENT_MAX_BYTES


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    app = create_app(tmp_path / 'data')
    with Session(app.state.engine) as db:
        db.add(Project(id=1, name='One'))
        db.commit()
    with TestClient(app) as client:
        yield app, client
    app.state.engine.dispose()


def test_import_revalidates_extracted_utf8_size_before_any_write(state, monkeypatch):
    app, client = state
    from backend.app.integrations import chatgpt_share
    monkeypatch.setattr(chatgpt_share, 'read_transcript', lambda _: ('Large source', 'é' * (NOTE_CONTENT_MAX_BYTES // 2 + 1)))
    response = client.post('/api/projects/1/notes/chatgpt-share', json={'url': 'https://chatgpt.com/share/11111111-1111-1111-1111-111111111111'})
    assert response.status_code == 502
    assert len(response.content) < 500
    with Session(app.state.engine) as db:
        assert list(db.scalars(select(Note))) == []
        assert list(db.scalars(select(AuditEvent))) == []


def test_existing_oversized_note_can_be_read_renamed_and_shortened_without_truncation(state):
    app, client = state
    original = 'é' * (NOTE_CONTENT_MAX_BYTES // 2 + 1)
    with Session(app.state.engine) as db:
        db.add(Note(id=1, project_id=1, title='Legacy import', content=original, kind='document'))
        db.commit()
    renamed = client.patch('/api/projects/1/notes/1', json={'title': 'Renamed'})
    assert renamed.status_code == 200
    assert renamed.json()['content'] == original
    assert client.get('/api/projects/1/notes/1').json()['content'] == original
    shortened = client.patch('/api/projects/1/notes/1', json={'content': '\n  smaller exact source  \n'})
    assert shortened.status_code == 200
    assert shortened.json()['content'] == '\n  smaller exact source  \n'
