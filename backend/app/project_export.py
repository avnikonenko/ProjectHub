"""Project-scoped JSON exports spooled to disk before the HTTP response starts."""
from base64 import b64encode
import json
import tempfile

from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from starlette.background import BackgroundTask

from .models import (Answer, AuditEvent, CodexEvent, CodexSession, ContextPackage, ConversationImportSource,
                     Decision, Delivery, GenerationRevision, Note, ProjectAIInstructions,
                     ProjectBrief, ProjectHistoryBatch, ProjectKnowledge, ProjectQA,
                     ProjectScheme, Question, Task)
from .services import question_dict, require_project, serialize


def _write(handle, value):
    for chunk in json.JSONEncoder(ensure_ascii=False).iterencode(value):
        # Preserve escapes from older filesystem snapshots without aborting the backup.
        handle.write(chunk.encode('utf-8', errors='backslashreplace'))


def _array(handle, values):
    handle.write(b'[')
    for index, value in enumerate(values):
        if index:
            handle.write(b',')
        _write(handle, value)
    handle.write(b']')


def _source_copies(handle, db, project_id):
    source = ConversationImportSource
    rows = db.scalars(select(source).join(Note, (Note.id == source.note_id) &
        (Note.project_id == source.project_id)).where(source.project_id == project_id)
        .order_by(source.id).execution_options(yield_per=1))
    handle.write(b'[')
    for index, row in enumerate(rows):
        if index:
            handle.write(b',')
        handle.write(b'{"payload_base64":"')
        # Divisible by three, so only the last chunk can contain base64 padding.
        size = 48 * 1024
        for offset in range(0, len(row.payload), size):
            handle.write(b64encode(row.payload[offset:offset + size]))
        handle.write(b'"')
        for column in source.__table__.columns:
            if column.name != 'payload':
                handle.write(b',')
                _write(handle, column.name)
                handle.write(b':')
                _write(handle, getattr(row, column.name))
        handle.write(b'}')
    handle.write(b']')


def _response(handle, headers=None):
    size = handle.tell()
    handle.seek(0)

    def chunks():
        try:
            while chunk := handle.read(65536):
                yield chunk
        finally:
            handle.close()

    return StreamingResponse(chunks(), media_type='application/json',
                             headers={'Content-Length': str(size), **(headers or {})},
                             background=BackgroundTask(handle.close))


def audit_response(db, project_id, offset=0, limit=None):
    require_project(db, project_id)
    handle = tempfile.TemporaryFile(mode='w+b')
    try:
        query = select(AuditEvent).where(AuditEvent.project_id == project_id).order_by(AuditEvent.id).offset(offset)
        if limit is not None:
            query = query.limit(limit)
        _array(handle, (serialize(row) for row in db.scalars(query.execution_options(yield_per=1))))
        return _response(handle)
    except BaseException:
        handle.close()
        raise


def project_export_response(db, project_id):
    # Keep the published brief/Knowledge pair and its revisions in one read
    # snapshot. SQLite's legacy driver otherwise does not BEGIN on SELECT.
    connection = db.connection()
    owns_snapshot = not connection.connection.driver_connection.in_transaction
    if owns_snapshot:
        connection.exec_driver_sql('BEGIN')
    project = require_project(db, project_id)
    handle = tempfile.TemporaryFile(mode='w+b')
    try:
        handle.write(b'{"project":')
        _write(handle, serialize(project))
        handle.write(b',"schema_version":1')
        tables = [('questions', Question), ('decisions', Decision), ('tasks', Task), ('notes', Note),
                  ('answers', Answer), ('audit', AuditEvent), ('sessions', CodexSession),
                  ('contexts', ContextPackage), ('deliveries', Delivery), ('events', CodexEvent),
                  ('generation_revisions', GenerationRevision), ('history_batches', ProjectHistoryBatch)]
        for key, model in tables:
            handle.write(b',')
            _write(handle, key)
            handle.write(b':')
            ordering = (model.pinned.desc(), model.id) if model is Task else (
                model.fingerprint if model is ProjectHistoryBatch else model.id,)
            rows = db.scalars(select(model).where(model.project_id == project_id)
                              .order_by(*ordering).execution_options(yield_per=1))
            _array(handle, (question_dict(db, row) if model is Question else serialize(row) for row in rows))
        handle.write(b',"conversation_import_sources":')
        _source_copies(handle, db, project_id)
        for key, model in [('brief', ProjectBrief), ('qa', ProjectQA), ('scheme', ProjectScheme),
                           ('knowledge', ProjectKnowledge), ('ai_instructions', ProjectAIInstructions)]:
            handle.write(b',')
            _write(handle, key)
            handle.write(b':')
            row = db.get(model, project_id)
            _write(handle, serialize(row) if row is not None else None)
        for key, table, order in [('history_turns', 'codex_history_turns', 'id'),
                                  ('history_index', 'codex_history_index', 'project_id')]:
            handle.write(b',')
            _write(handle, key)
            handle.write(b':')
            rows = db.execute(text(f'SELECT * FROM {table} WHERE project_id=:pid ORDER BY {order}'),
                              {'pid': project_id}).mappings()
            _array(handle, (dict(row) for row in rows))
        handle.write(b'}')
        # The file is complete: a slow download must not retain a SQLite read
        # lock and block publication or edits. Never end a caller-owned txn.
        if owns_snapshot:
            db.rollback()
        return _response(handle)
    except BaseException:
        handle.close()
        if owns_snapshot:
            db.rollback()
        raise
