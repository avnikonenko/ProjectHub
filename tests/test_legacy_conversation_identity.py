import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.integrations.codex.knowledge import extract_batch, extraction_configuration
from backend.app.integrations.codex.knowledge_sources import build_windows, load_sources
from backend.app.integrations.codex.scheme_knowledge import _origin
from backend.app.main import create_app
from backend.app.models import ConversationImportSource, Note, ProjectKnowledge, Task


def test_legacy_reused_ids_cannot_rebind_original_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    app = create_app(tmp_path)

    class Extractor:
        calls = 0

        def generate_brief(self, prompt, schema):
            self.calls += 1
            return json.dumps({'items': []})

    with TestClient(app) as client:
        pid = client.post('/api/projects', json={'name': 'Tool library'}).json()['id']
        project = f'/api/projects/{pid}'
        body = {'filename': 'transcript.json', 'source_url': 'https://example.test/original',
                'content': json.dumps({'title': 'Checkouts', 'messages': [
                    {'id': 'user-one', 'role': 'user', 'content': 'Use the blue sign-out sheet.'},
                    {'id': 'answer-one', 'role': 'assistant', 'content': 'Understood.'},
                ]})}
        preview = client.post(project + '/conversations/preview', json=body).json()
        response = client.post(project + '/conversations/import', json={**body, 'fingerprint': preview['fingerprint']})
        assert response.status_code == 201
        original = response.json()
        note_path = project + f'/notes/{original["id"]}'
        copies_path = project + f'/conversations/{original["id"]}/sources'
        copy = client.get(copies_path).json()[0]
        file_path = copies_path + f'/{copy["id"]}'
        original_bundle = load_sources(app.state.engine, pid)
        old_source = original_bundle['sources'][0]
        extractor = Extractor()
        configuration = extraction_configuration(extractor)
        original_window = build_windows(original_bundle)[0]
        assert extract_batch(app.state.engine, pid, extractor, original_window, configuration) == ([], False)
        assert extract_batch(app.state.engine, pid, extractor, original_window, configuration) == ([], True)
        ref = {key: old_source[key] for key in ('revision', 'source_key', 'role', 'conversation_id')}
        ref.update(source_id=old_source['id'], excerpt=old_source['text'])
        snapshot = {'project_id': pid, 'publication_id': 'original', 'extraction_version': 'fixture',
                    'source_manifest': original_bundle['manifest'], 'sources': original_bundle['sources'],
                    'items': [{'id': 'an-item', 'statement': 'Use a sign-out sheet.', 'scope': 'checkout',
                               'design_status': 'AGREED', 'evidence': [ref], 'extraction_version': 'fixture'}]}

        assert client.delete(note_path).status_code == 204
        replacement_date = '2026-11-24T00:00:01.000000+00:00'
        # Simulate data carried over from before IDs were made monotonic.
        with Session(app.state.engine) as db:
            db.add(Note(id=original['id'], project_id=pid, title=original['title'], content=original['content'],
                        kind='document', reference='https://example.test/replacement',
                        conversation=original['conversation'], created_at=replacement_date))
            db.flush()
            db.add(ConversationImportSource(id=copy['id'], project_id=pid, note_id=original['id'],
                    filename=body['filename'], reference='https://example.test/replacement',
                    content_hash=copy['content_hash'], payload=body['content'].encode()))
            db.add(ProjectKnowledge(project_id=pid, snapshot=snapshot))
            db.commit()

        assert client.get(note_path, params={'transcript': 'true'}).status_code == 404
        assert client.get(note_path, params={'transcript': 'true', 'created_at': original['created_at']}).status_code == 404
        assert client.get(note_path, params={'transcript': 'true', 'created_at': replacement_date}).status_code == 200
        assert client.delete(note_path).status_code == 409
        assert client.delete(note_path, params={'created_at': original['created_at']}).status_code == 409
        assert client.patch(note_path, json={'title': 'Wrong conversation'}).status_code == 409
        assert client.patch(note_path, params={'created_at': replacement_date},
                            json={'title': original['title']}).status_code == 200
        assert client.get(file_path).status_code == 404
        assert client.get(file_path, params={'created_at': original['created_at'], 'content_hash': copy['content_hash']}).status_code == 404
        assert client.get(file_path, params={'created_at': replacement_date, 'content_hash': '0' * 64}).status_code == 404
        assert client.get(file_path, params={'created_at': replacement_date, 'content_hash': copy['content_hash']}).content == body['content'].encode()
        search = client.get(project + '/search/all', params={'q': 'blue', 'kind': 'conversations'}).json()
        assert 'created_at=' in search['groups'][0]['results'][0]['href']
        current = client.get(project + '/codex/knowledge').json()
        assert current['current_evidence_availability']['an-item'] == 'unavailable'
        new_bundle = load_sources(app.state.engine, pid, original_bundle['manifest'])
        new_source = new_bundle['sources'][0]
        assert new_source['id'] != old_source['id'] and new_source['revision'] == old_source['revision']
        assert next(entry for entry in new_bundle['manifest']['entries']
                    if entry['source_key'] == old_source['source_key'])['reason'] == 'replaced_local_record'
        assert extract_batch(app.state.engine, pid, extractor, build_windows(new_bundle)[0], configuration) == ([], False)
        assert extractor.calls == 2
        with Session(app.state.engine) as db:
            assert _origin(db, pid, ref, old_source)[:2] == ({}, 'unavailable')
            current_ref = {**ref, 'source_id': new_source['id']}
            navigation, availability, _ = _origin(db, pid, current_ref, new_source)
            assert availability == 'available' and navigation['note_created_at'] == replacement_date


def test_stale_task_mutations_cannot_change_a_previously_reused_id(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    app = create_app(tmp_path)
    with TestClient(app) as client:
        pid = client.post('/api/projects', json={'name': 'TODO identity'}).json()['id']
        tasks = f'/api/projects/{pid}/tasks'
        old = client.post(tasks, json={'title': 'Original TODO'}).json()
        path = f'{tasks}/{old["id"]}'
        assert client.delete(path).status_code == 204
        created_at = '2026-11-24T00:00:01+00:00'
        with Session(app.state.engine) as db:
            db.add(Task(id=old['id'], project_id=pid, title='Replacement TODO', created_at=created_at))
            db.commit()
        assert client.patch(path, json={'status': 'completed'}).status_code == 409
        assert client.delete(path, params={'created_at': old['created_at']}).status_code == 409
        changed = client.patch(path, params={'created_at': created_at}, json={'status': 'completed'})
        assert changed.status_code == 200 and changed.json()['title'] == 'Replacement TODO'
        assert client.delete(path, params={'created_at': created_at}).status_code == 204
