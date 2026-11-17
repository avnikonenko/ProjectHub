"""Delayed real manager callbacks retain their original project identity.

Temporary SQLite and actual session/callback wiring; Codex transport is mocked.
"""
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import CodexEvent, CodexSession, Project
from backend.app.integrations.codex.manager import CodexManager
from backend.app.integrations.codex.session_reassignment import reassign_session
from backend.app.integrations.codex.protocol import CodexError
from backend.tests.integrations.test_codex import FakeClient


def test_released_source_callbacks_cannot_write_into_destination_project(tmp_path, monkeypatch):
    engine, data_dir = initialize(tmp_path)
    manager = CodexManager(engine, data_dir, client_factory=FakeClient)
    clients = []

    def discussion_client(cwd):
        client = FakeClient()
        clients.append(client)
        return client

    monkeypatch.setattr(manager, '_discussion_client', discussion_client)
    try:
        with Session(engine) as db:
            source, target = Project(name='Source'), Project(name='Target')
            db.add_all([source, target]); db.flush()
            session = CodexSession(project_id=source.id, thread_id='external-thread',
                                   title='External discussion', status='linked')
            db.add(session); db.commit(); db.refresh(session)
            sid = session.id
            # Capture actual production callback wiring without promoting this
            # external association into a ProjectHub discussion session.
            client = discussion_client(str(data_dir))
            manager._bind_session_callbacks(client, session)
            manager.clients[sid] = client
        old_event = clients[0].on_event
        old_disconnect = clients[0].on_disconnect
        with Session(engine) as db:
            moved = reassign_session(db, 1, sid, 2, manager)
        assert moved['project_id'] == 2 and clients[0].closed

        # A reader may already hold these callbacks when close returns. The
        # thread ID stays identical through reassignment, so it is insufficient
        # by itself to authorize storing the delayed message in the new project.
        identity_reads = []
        def record_identity_read(connection, cursor, statement, parameters, context, many):
            if statement.lstrip().startswith('SELECT') and 'FROM codex_sessions' in statement:
                identity_reads.append(connection.connection.driver_connection.in_transaction)
        event.listen(engine, 'before_cursor_execute', record_identity_read)
        try:
            old_event('item/completed', {'threadId': 'external-thread', 'turnId': 'late',
                      'item': {'type': 'agentMessage', 'text': 'SOURCE_ONLY_RESPONSE'}})
            old_disconnect()
        finally:
            event.remove(engine, 'before_cursor_execute', record_identity_read)
        assert len(identity_reads) == 2 and all(identity_reads), (
            'Callback ownership checks must share the write transaction with their mutations.')
        with Session(engine) as db:
            assert list(db.scalars(select(CodexEvent))) == []
            current = db.get(CodexSession, sid)
            assert current.project_id == 2
            assert current.last_error == moved['last_error']

            manager.resume(current, db.get(Project, 2))
        clients[1].on_event('item/completed', {'threadId': 'external-thread', 'turnId': 'current',
                            'item': {'type': 'agentMessage', 'text': 'TARGET_RESPONSE'}})
        with Session(engine) as db:
            events = list(db.scalars(select(CodexEvent)))
            assert len(events) == 1 and events[0].project_id == 2
            assert events[0].payload['item']['text'] == 'TARGET_RESPONSE'
    finally:
        manager.close()
        engine.dispose()


def test_stale_source_resume_is_rejected_before_opening_a_transport(tmp_path, monkeypatch):
    engine, data_dir = initialize(tmp_path)
    manager = CodexManager(engine, data_dir, client_factory=FakeClient)
    try:
        with Session(engine) as db:
            source, target = Project(name='Source'), Project(name='Target')
            db.add_all([source, target]); db.flush()
            old_session = CodexSession(project_id=source.id, thread_id='external-thread',
                                       title='External discussion', status='linked')
            db.add(old_session); db.commit()
            db.refresh(old_session); db.refresh(source)
            db.expunge_all()
        with Session(engine) as db:
            reassign_session(db, 1, old_session.id, 2, manager)
        monkeypatch.setattr(manager, '_discussion_client', lambda cwd: pytest.fail(
            'A stale source-project request opened a Codex transport.'))
        with pytest.raises(CodexError, match='association changed'):
            manager.resume(old_session, source)
        with Session(engine) as db:
            current = db.get(CodexSession, old_session.id)
            assert current.project_id == 2 and current.status == 'linked'
    finally:
        manager.close()
        engine.dispose()
