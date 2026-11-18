"""Remove one project's local state and preserve its retired identity."""
from contextlib import ExitStack, contextmanager
import logging
import os
from pathlib import Path
import re
import stat
from threading import Lock

from fastapi import HTTPException
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .integrations.codex.generation_jobs import generation_lock
from .integrations.codex.history_search import _index_lock
from .models import (Answer, AuditEvent, CodexEvent, CodexSession, ContextPackage, Decision,
                     Delivery, GenerationRevision, Note, Project, ProjectBrief, ProjectGenerationAttempt,
                     ProjectHistoryBatch, ProjectQA, Question, RemovedProject, Task)
from .services import require_project

log = logging.getLogger(__name__)
_UPLOAD_NAME = re.compile(r'[0-9a-f]{32}\.(?:md|txt)')


class ProjectRemovalGate:
    """Allow concurrent ordinary writes and reserve an exclusive removal window."""

    def __init__(self):
        self._lock = Lock()
        self._writers = 0
        self._removing = False

    @contextmanager
    def mutation(self, *, removal):
        with self._lock:
            if self._removing or (removal and self._writers):
                raise HTTPException(409, 'Wait for active ProjectHub changes to finish before trying again.')
            if removal:
                self._removing = True
            else:
                self._writers += 1
        try:
            yield
        finally:
            with self._lock:
                if removal:
                    self._removing = False
                else:
                    self._writers -= 1


def next_project_id(db):
    # Allocate under the SQLite write lock so a removed highest ID stays retired.
    db.execute(text('BEGIN IMMEDIATE'))
    highest = db.scalar(text('SELECT max(COALESCE((SELECT max(id) FROM projects), 0), '
                             'COALESCE((SELECT max(project_id) FROM removed_projects), 0))'))
    return highest + 1


def _uploaded_names(db, project_id):
    # The importer alone emits this audit action. Note references are editable
    # and may contain arbitrary user input, or the imported note may be gone.
    reference = AuditEvent.after['reference'].as_string()
    return sorted({name for name in db.scalars(select(reference).where(
        AuditEvent.project_id == project_id, AuditEvent.entity_type == 'notes',
        AuditEvent.action == 'imported', AuditEvent.after['kind'].as_string() == 'document',
    )) if isinstance(name, str) and _UPLOAD_NAME.fullmatch(name)})


def _uploads_overlap_repository(db, directory):
    uploads = directory / 'uploads'
    roots = db.scalars(select(Project.repository_path).where(Project.repository_path.is_not(None)))
    for value in roots:
        root = Path(value)
        if uploads.is_relative_to(root):
            return True
        try:
            if uploads.resolve().is_relative_to(root.resolve()):
                return True
        except (OSError, RuntimeError):
            # A registration can point to a path that no longer resolves.
            continue
    return False


def _unlink_upload(name, descriptor):
    if not isinstance(name, str) or not _UPLOAD_NAME.fullmatch(name):
        return
    try:
        mode = os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_mode
        if stat.S_ISREG(mode):
            os.unlink(name, dir_fd=descriptor)
    except FileNotFoundError:
        pass


def retry_removed_uploads(db, directory, project_id=None):
    """Apply committed file cleanup now and on startup after an interrupted removal."""
    try:
        _retry_removed_uploads(db, directory, project_id)
    except SQLAlchemyError:
        db.rollback()
        log.warning('Could not finish recording uploads cleanup for removed projects.')


def _retry_removed_uploads(db, directory, project_id):
    query = select(RemovedProject).where(RemovedProject.pending_uploads != [])
    if project_id is not None:
        query = query.where(RemovedProject.project_id == project_id)
    pending = list(db.scalars(query))
    if not pending:
        return
    if _uploads_overlap_repository(db, directory):
        # Cleanup may have been queued before this repository was registered.
        # Retire the queued deletions so they cannot touch it on a later restart.
        for row in pending:
            row.pending_uploads = []
        db.commit()
        return
    try:
        descriptor = os.open(directory / 'uploads', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        for row in pending:
            row.pending_uploads = []
    except OSError:
        log.warning('Could not open the local uploads directory to finish project removal.')
        return
    else:
        try:
            for row in pending:
                remaining = []
                for name in row.pending_uploads:
                    try:
                        _unlink_upload(name, descriptor)
                    except OSError:
                        remaining.append(name)
                if remaining:
                    log.warning('Could not remove all local uploaded copies for project %d.', row.project_id)
                row.pending_uploads = remaining
        finally:
            os.close(descriptor)
    db.commit()


def remove_project(db, directory, project_id, manager):
    require_project(db, project_id)
    db.rollback()
    with ExitStack() as locks:
        guards = [(generation_lock, 'Wait for AI generation to finish before removing a project.'),
                  (_index_lock, 'Wait for history indexing to finish before removing a project.')]
        if manager is not None:
            guards.append((manager.lock, 'Wait for the current Codex session operation to finish.'))
        for lock, message in guards:
            if not lock.acquire(blocking=False):
                raise HTTPException(409, message)
            locks.callback(lock.release)

        db.execute(text('BEGIN IMMEDIATE'))
        project = require_project(db, project_id)
        sessions = db.execute(select(CodexSession.id, CodexSession.status).where(
            CodexSession.project_id == project_id)).all()
        if any(session.status == 'running' for session in sessions) or db.scalar(
            select(Delivery.id).where(Delivery.project_id == project_id,
                                     Delivery.status.in_(('sending', 'sent'))).limit(1)
        ) is not None:
            raise HTTPException(409, 'Wait for the active Codex turn to finish before removing this project.')
        if db.scalar(select(ProjectGenerationAttempt.kind).where(
            ProjectGenerationAttempt.project_id == project_id,
            ProjectGenerationAttempt.state.in_(('waiting', 'running')),
        ).limit(1)) is not None:
            raise HTTPException(409, 'Wait for queued or active AI generation to finish before removing this project.')

        try:
            # Check before deleting the project row so its own repository is protected too.
            pending_uploads = [] if _uploads_overlap_repository(db, directory) else _uploaded_names(db, project_id)
            db.add(RemovedProject(project_id=project_id, pending_uploads=pending_uploads))
            db.execute(text('DELETE FROM codex_history_turns WHERE project_id=:pid'), {'pid': project_id})
            db.execute(text('DELETE FROM codex_history_index WHERE project_id=:pid'), {'pid': project_id})
            # Break the project-local Question -> accepted Answer -> Question cycle.
            db.execute(update(Question).where(Question.project_id == project_id,
                Question.accepted_answer_id.is_not(None)).values(accepted_answer_id=None))
            for record in (Delivery, Task, CodexEvent, ContextPackage, CodexSession, Decision,
                           Answer, Question, Note, AuditEvent, ProjectBrief, ProjectHistoryBatch,
                           ProjectQA, GenerationRevision):
                db.execute(delete(record).where(record.project_id == project_id))
            # Existing FTS triggers and ON DELETE CASCADE maintain derived indexes
            # and the remaining project tables in the same transaction.
            db.delete(project)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, 'This project has inconsistent linked records. No project data was removed.') from exc

        if manager is not None:
            for session in sessions:
                try:
                    manager.release_session(session.id)
                except Exception:
                    # The local project is already removed and detached client
                    # callbacks recheck ownership before writing.
                    log.warning('Could not close a Codex transport for removed project %d.', project_id)
    retry_removed_uploads(db, directory, project_id)
