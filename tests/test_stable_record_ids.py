from hashlib import sha256
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.app.models import (
    ActiveGenerationRevision, AuditEvent, CodexEvent, CodexSession, ConversationImportSource, GenerationRevision,
    Note, Project, Task,
)


def _migration_engine(db_path):
    engine = create_engine(f'sqlite:///{db_path}')

    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')

    return engine


def _migrate(engine, version, *, downgrade=False):
    cfg = Config()
    location = str(Path(__file__).resolve().parents[1] / 'migrations')
    cfg.set_main_option('script_location', location.replace('%', '%%'))
    with engine.begin() as connection:
        cfg.attributes['connection'] = connection
        migrate = command.downgrade if downgrade else command.upgrade
        migrate(cfg, version)


def _copy(note, payload, source_id=None):
    return ConversationImportSource(
        id=source_id, project_id=note.project_id, note_id=note.id, filename='chat.json',
        reference=note.reference, content_hash=sha256(payload).hexdigest(), payload=payload,
    )


def test_upgrade_preserves_rows_search_indexes_and_historical_id_floor(tmp_path):
    engine = _migration_engine(tmp_path / 'projecthub.sqlite3')
    _migrate(engine, '0029')
    original = b'{"source": "original"}\n'
    with Session(engine) as db:
        db.add(Project(id=1, name='Migration sample'))
        db.flush()
        db.add(CodexSession(id=11, project_id=1, thread_id='migration-fixture'))
        db.flush()
        kept_note = Note(id=1, project_id=1, title='Saved transcript', content='cedar original', kind='document',
                         reference='https://example.test/original', conversation={'fingerprint': 'sample'})
        db.add_all((kept_note, Note(id=29, project_id=1, title='Already deleted')))
        db.flush()
        db.add_all((
            _copy(kept_note, original, source_id=47),
            AuditEvent(project_id=1, entity_type='notes', entity_id=29, action='deleted'),
            AuditEvent(project_id=1, entity_type='tasks', entity_id=79, action='deleted'),
            Task(id=66, project_id=1, title='spruce retained task', pinned=True, session_id=11),
            CodexEvent(id=71, project_id=1, session_id=11, method='item/completed',
                       payload={'item': {'type': 'agentMessage', 'text': 'Saved response'}}),
            GenerationRevision(id=61, project_id=1, kind='brief', generated_at='2026-11-22T10:00:00Z',
                               snapshot={'generated_at': '2026-11-22T10:00:00Z', 'summary': 'Saved result'}),
        ))
        db.flush()
        db.delete(db.get(Note, 29))
        db.add(ActiveGenerationRevision(project_id=1, kind='brief', revision_id=61))
        db.commit()

    _migrate(engine, 'head')

    with Session(engine) as db:
        kept_note = db.get(Note, 1)
        assert kept_note.conversation == {'fingerprint': 'sample'}
        assert db.get(ConversationImportSource, 47).payload == original
        assert db.get(ActiveGenerationRevision, (1, 'brief')).revision_id == 61
        assert db.get(GenerationRevision, 61).snapshot['summary'] == 'Saved result'
        assert db.get(Task, 66).pinned and db.get(Task, 66).session_id == 11
        assert db.get(CodexEvent, 71).payload['item']['text'] == 'Saved response'
        assert db.execute(text('PRAGMA foreign_keys')).scalar() == 1
        assert db.execute(text('PRAGMA foreign_key_check')).all() == []
        assert db.execute(text("SELECT entity_id FROM search_index WHERE search_index MATCH 'cedar'")).scalars().all() == [1]
        index_defs = db.execute(text("SELECT name FROM sqlite_schema WHERE type='index'")).scalars()
        assert {'ix_notes_project_conversation_fingerprint', 'ix_conversation_import_sources_note',
                'ix_generation_revisions_project_id', 'ix_tasks_project_id', 'ix_codex_events_session_id'} <= set(index_defs)
        assert db.execute(text("SELECT entity_id FROM search_index WHERE search_index MATCH 'spruce'")).scalar() == 66
        kept_note.content = 'birch replacement'
        db.flush()
        assert db.execute(text("SELECT entity_id FROM search_index WHERE search_index MATCH 'cedar'")).first() is None
        assert db.execute(text("SELECT entity_id FROM search_index WHERE search_index MATCH 'birch'")).scalar() == 1
        db.delete(kept_note)
        db.delete(db.get(Task, 66))
        db.delete(db.get(CodexEvent, 71))
        db.delete(db.get(GenerationRevision, 61))
        db.commit()

    # The table rebuild must not discard post-upgrade sequence values if
    # upgrading again after the highest live records have been deleted.
    _migrate(engine, '0029', downgrade=True)
    _migrate(engine, 'head')

    with Session(engine) as db:
        assert db.get(ConversationImportSource, 47) is None
        assert db.get(ActiveGenerationRevision, (1, 'brief')) is None
        assert db.execute(text("SELECT entity_id FROM search_index WHERE search_index MATCH 'birch'")).first() is None
        assert db.execute(text("SELECT entity_id FROM search_index WHERE search_index MATCH 'spruce'")).first() is None
        note = Note(project_id=1, title='Later import', content='willow fresh')
        revision = GenerationRevision(project_id=1, kind='brief', generated_at='2026-11-23T10:00:00Z', snapshot={})
        task = Task(project_id=1, title='cypress fresh task', session_id=11)
        reply = CodexEvent(project_id=1, session_id=11, method='item/completed', payload={'item': {'text': 'New reply'}})
        db.add_all((note, revision, task, reply))
        db.flush()
        copied = _copy(note, b'new bytes')
        db.add(copied)
        db.flush()
        assert note.id > 29 and revision.id > 61 and copied.id > 47
        assert task.id > 79 and reply.id > 71
        assert db.execute(text("SELECT entity_id FROM search_index WHERE search_index MATCH 'willow'")).scalar() == note.id
        assert db.execute(text("SELECT entity_id FROM search_index WHERE search_index MATCH 'cypress'")).scalar() == task.id
    engine.dispose()


def test_removing_imported_conversation_retires_original_urls(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    with TestClient(create_app(tmp_path)) as client:
        project = client.post('/api/projects', json={'name': 'Source identity sample'}).json()
        base = f"/api/projects/{project['id']}"
        messages = [{'role': 'user', 'id': 'u1', 'content': 'Check the printed calendar.'},
                    {'role': 'assistant', 'id': 'a1', 'content': 'The printed calendar is available.'}]

        def import_file(label):
            body = {'filename': 'chat.json', 'source_url': f'https://example.test/{label}',
                    'content': json.dumps({'title': label, 'messages': messages})}
            preview = client.post(base + '/conversations/preview', json=body)
            assert preview.status_code == 200
            imported = client.post(base + '/conversations/import',
                                   json={**body, 'fingerprint': preview.json()['fingerprint']})
            assert imported.status_code == 201
            note_id = imported.json()['id']
            copies = client.get(base + f'/conversations/{note_id}/sources').json()
            return note_id, copies[0]['id']

        old_note, old_copy = import_file('original')
        assert client.delete(base + f'/notes/{old_note}').status_code == 204
        new_note, new_copy = import_file('different-source')
        assert new_note > old_note and new_copy > old_copy
        assert client.get(base + f'/notes/{old_note}').status_code == 404
        assert client.get(base + f'/conversations/{old_note}/sources/{old_copy}').status_code == 404
        assert client.get(base + f'/conversations/{new_note}/sources/{new_copy}').status_code == 200
