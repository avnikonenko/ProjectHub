"""Audited reassignment of external-session links without moving project discussions."""
from contextlib import ExitStack

from fastapi import HTTPException
from sqlalchemy import select, text

from ...models import AuditEvent, CodexEvent, CodexSession, ContextPackage, Delivery, Task, utcnow
from ...services import require_project, scoped
from .brief import generation_lock
from .history_search import _index_lock, update_active_count
from .manager import record_dict


def check_session_reassignment(db, source_project_id, session_id, target_project_id, *, require_unlinked):
    row = scoped(db, CodexSession, source_project_id, session_id)
    require_project(db, target_project_id)
    if source_project_id == target_project_id:
        raise HTTPException(422, 'Choose a different destination project.')
    if row.status == 'running':
        raise HTTPException(409, 'Wait for this session to finish before moving it.')
    if row.status != 'linked':
        raise HTTPException(409, 'Only external read-only session links can move between projects. '
                            'ProjectHub discussion sessions keep their original project context.')
    if require_unlinked and row.is_linked:
        raise HTTPException(409, 'Thread is linked to another project. Unlink it there or explicitly move the session.')
    for model in (Task, ContextPackage, Delivery, CodexEvent):
        if db.scalar(select(model.id).where(model.session_id == session_id).limit(1)) is not None:
            raise HTTPException(409, 'This session has project-specific discussion or task records. '
                                'They must stay with their original project; use a separate session for the destination.')
    if db.scalar(text('SELECT id FROM codex_history_turns WHERE session_id=:sid '
                      'AND project_id<>:pid LIMIT 1'), {'sid': session_id, 'pid': source_project_id}) is not None:
        raise HTTPException(409, 'This session has inconsistent saved-history ownership and cannot be moved.')
    return row


def reassign_session(db, source_project_id, session_id, target_project_id, manager, *, require_unlinked=False):
    """Move one idle external association and its local index in one transaction.

    No Codex transport is opened. Existing transports are released before the
    write transaction because their close callbacks can write to this database.
    Original generated reports and their historical quotations remain untouched.
    """
    with ExitStack() as locks:
        for lock, message in ((generation_lock, 'Wait for AI generation to finish before moving a session.'),
                              (_index_lock, 'Wait for history indexing to finish before moving a session.'),
                              (manager.lock, 'Wait for this session operation to finish before moving it.')):
            if not lock.acquire(blocking=False):
                raise HTTPException(409, message)
            locks.callback(lock.release)
        try:
            check_session_reassignment(db, source_project_id, session_id, target_project_id,
                                       require_unlinked=require_unlinked)
            db.rollback()
            manager.release_session(session_id)
            db.expire_all()
            # A real SQLite write transaction prevents concurrent index/session
            # writers from changing ownership between validation and commit.
            db.execute(text('BEGIN IMMEDIATE'))
            row = check_session_reassignment(db, source_project_id, session_id, target_project_id,
                                             require_unlinked=require_unlinked)
            before = record_dict(row)
            row.project_id = target_project_id
            row.is_linked = True
            now = utcnow()
            require_project(db, source_project_id).updated_at = now
            require_project(db, target_project_id).updated_at = now
            db.flush()
            db.execute(text('UPDATE codex_history_turns SET project_id=:target '
                            'WHERE project_id=:source AND session_id=:sid'),
                       {'target': target_project_id, 'source': source_project_id, 'sid': session_id})
            # The destination may never have had an index. Its membership was
            # updated now; the session retains its actual history-read timestamp.
            db.execute(text('INSERT INTO codex_history_index(project_id,indexed_at,indexed_turns) '
                            'VALUES (:pid,:at,0) ON CONFLICT(project_id) DO NOTHING'),
                       {'pid': target_project_id, 'at': now})
            update_active_count(db, source_project_id)
            update_active_count(db, target_project_id)
            after = record_dict(row)
            for project_id, action in ((source_project_id, 'session_moved_out'),
                                       (target_project_id, 'session_moved_in')):
                db.add(AuditEvent(project_id=project_id, entity_type='codex_sessions', entity_id=row.id,
                                  action=action, before=before, after=after))
            db.commit()
            return record_dict(row)
        except BaseException:
            db.rollback()
            raise
