"""Real SQLite serialization of session assignments against project moves."""
from threading import RLock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import CodexSession, Project, Task
from backend.app.services import validate_links
from backend.app.integrations.codex.router import (
    SessionDetailsInput, SessionSyncInput, configure_session_sync, update_session_details,
)
from backend.app.integrations.codex.session_reassignment import reassign_session


@pytest.fixture
def state(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(id=1, name='Source'), Project(id=2, name='Target')]); db.flush()
        db.add(CodexSession(id=1, project_id=1, thread_id='external', title='Original', status='linked'))
        db.commit()
    manager = SimpleNamespace(lock=RLock(), release_session=lambda sid: None)
    try:
        yield engine, manager
    finally:
        engine.dispose()


def test_task_ownership_lookup_holds_write_lock_against_a_move(state):
    engine, manager = state
    checked = []
    def observe(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().startswith('SELECT') and 'FROM codex_sessions' in statement:
            checked.append(connection.connection.driver_connection.in_transaction)
    event.listen(engine, 'before_cursor_execute', observe)
    try:
        with Session(engine) as writer:
            validate_links(writer, 1, {'session_id': 1})
            assert checked and all(checked)
            event.remove(engine, 'before_cursor_execute', observe)
            with Session(engine) as mover:
                mover.execute(text('PRAGMA busy_timeout=0'))
                with pytest.raises(OperationalError, match='locked'):
                    reassign_session(mover, 1, 1, 2, manager)
            writer.add(Task(project_id=1, title='Source task', session_id=1))
            writer.commit()
        with Session(engine) as mover:
            with pytest.raises(HTTPException) as blocked:
                reassign_session(mover, 1, 1, 2, manager)
            assert blocked.value.status_code == 409
            assert mover.get(CodexSession, 1).project_id == 1
    finally:
        if event.contains(engine, 'before_cursor_execute', observe):
            event.remove(engine, 'before_cursor_execute', observe)


def test_moved_session_cannot_be_assigned_from_stale_source_identity_map(state):
    engine, manager = state
    with Session(engine) as stale:
        original = stale.get(CodexSession, 1)
        assert original.project_id == 1
        with Session(engine) as mover:
            reassign_session(mover, 1, 1, 2, manager)
        with pytest.raises(HTTPException) as rejected:
            validate_links(stale, 1, {'session_id': 1})
        assert rejected.value.status_code == 404
        stale.rollback()
    with Session(engine) as db:
        assert list(db.scalars(select(Task))) == []
        validate_links(db, 2, {'session_id': 1})
        db.add(Task(project_id=2, title='Target task', session_id=1)); db.commit()


@pytest.mark.parametrize('existing_transaction', [False, True])
def test_bulk_session_assignments_reuse_real_transactions(state, existing_transaction):
    engine, _ = state
    with Session(engine) as db:
        if existing_transaction:
            db.execute(text('BEGIN'))
        for number in range(3):
            validate_links(db, 1, {'session_id': 1})
            db.add(Task(project_id=1, title=f'Task {number}', session_id=1))
            db.flush()
        db.commit()
        assert len(list(db.scalars(select(Task)))) == 3


@pytest.mark.parametrize('action', ['details', 'sync'])
def test_stale_source_session_mutations_reject_new_owner(state, action):
    engine, manager = state
    with Session(engine) as stale:
        stale.get(CodexSession, 1)
        with Session(engine) as mover:
            reassign_session(mover, 1, 1, 2, manager)
        with pytest.raises(HTTPException) as rejected:
            if action == 'details':
                update_session_details(1, 1, SessionDetailsInput(title='Wrong source update'), stale)
            else:
                configure_session_sync(1, 1, SessionSyncInput(enabled=False), stale)
        assert rejected.value.status_code == 404
        stale.rollback()
    with Session(engine) as db:
        current = db.get(CodexSession, 1)
        assert current.project_id == 2 and current.title == 'Original' and current.auto_sync is True
