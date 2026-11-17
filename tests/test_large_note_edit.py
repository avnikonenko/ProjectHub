"""Preserve imported text while retaining bounded note writes and partial PATCH."""
import pytest
from pydantic import ValidationError

from backend.app.schemas import NoteCreate, NotePatch, NOTE_CONTENT_MAX_BYTES


@pytest.mark.parametrize('model', [NoteCreate, NotePatch])
def test_large_note_content_keeps_original_whitespace(model):
    content = '\n  ' + 'x' * 210000 + '  \n'
    values = {'content': content, **({'title': 'Imported note'} if model is NoteCreate else {})}
    assert model(**values).content == content


def test_note_patch_keeps_omitted_fields_and_other_field_limits():
    assert NotePatch(title=' Updated title ').model_dump(exclude_unset=True) == {'title': 'Updated title'}
    with pytest.raises(ValidationError):
        NotePatch(reference='x' * 200001)


@pytest.mark.parametrize('model', [NoteCreate, NotePatch])
def test_note_content_is_bounded_in_utf8_bytes(model):
    # This fits the character limit but exceeds the imported-text byte ceiling.
    value = 'é' * (NOTE_CONTENT_MAX_BYTES // 2 + 1)
    values = {'content': value, **({'title': 'Too large'} if model is NoteCreate else {})}
    with pytest.raises(ValidationError, match='16 MiB'):
        model(**values)


def test_imported_document_can_be_saved_by_full_editor_form(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    app = create_app(tmp_path / 'data')
    content = '\n  ' + 'Original structure evidence. ' * 8000 + '  \n'
    with TestClient(app) as client:
        project = client.post('/api/projects', json={'name': 'Import editing'}).json()
        base = f"/api/projects/{project['id']}"
        imported = client.post(base + '/documents', files={'file': ('large.md', content.encode(), 'text/markdown')})
        assert imported.status_code == 201, imported.text
        note = imported.json()
        updated = client.patch(base + f"/notes/{note['id']}", json={
            'title': 'Updated title', 'content': note['content'], 'kind': note['kind'], 'reference': note['reference'],
        })
        assert updated.status_code == 200, updated.text
        assert updated.json()['content'] == content
    app.state.engine.dispose()


def test_large_note_json_writes_have_a_scoped_body_allowance(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    app = create_app(tmp_path / 'data')
    content = '\n' + 'x' * (3 * 1024 * 1024) + '\n'
    with TestClient(app) as client:
        project = client.post('/api/projects', json={'name': 'Large note writes'}).json()
        base = f"/api/projects/{project['id']}"
        created = client.post(base + '/notes', json={'title': 'Saved transcript', 'content': content})
        assert created.status_code == 201, created.text[:500]
        note_id = created.json()['id']
        updated = client.patch(base + f'/notes/{note_id}', json={'content': content, 'title': 'Edited title'})
        assert updated.status_code == 200, updated.text[:500]
        assert updated.json()['content'] == content
        # Larger note bodies do not silently enlarge every other mutation route.
        unrelated = client.patch(base, json={'description': content})
        assert unrelated.status_code == 413
    app.state.engine.dispose()
