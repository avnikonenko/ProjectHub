"""Project-scoped exports spooled to disk before the HTTP response starts."""
from base64 import b64encode
from contextlib import contextmanager
from io import BufferedWriter
from itertools import groupby
import json
from pathlib import Path
import re
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.responses import StreamingResponse
from sqlalchemy import LargeBinary, func, select, text
from sqlalchemy.orm import defer
from starlette.background import BackgroundTask

from .models import (Answer, AuditEvent, CodexEvent, CodexSession, ContextPackage, ConversationImportSource,
                     Decision, Delivery, GenerationRevision, Note, ProjectAIInstructions,
                     ProjectBrief, ProjectFocusedScheme, ProjectGenerationAttempt, ProjectHistoryBatch, ProjectKnowledge, ProjectQA,
                     ProjectScheme, Question, Task)
from .services import question_dict, require_project, serialize

_BLOCK_BYTES = 65536
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024
_UPLOAD_NAME = re.compile(r'[0-9a-f]{32}\.(?:md|txt)')
_SINGLETONS = (('brief', ProjectBrief), ('qa', ProjectQA), ('scheme', ProjectScheme), ('focused_scheme', ProjectFocusedScheme),
               ('knowledge', ProjectKnowledge), ('ai_instructions', ProjectAIInstructions))
_HISTORY_ROWS = ('FROM codex_history_turns h JOIN codex_sessions s '
                 'ON s.id=h.session_id AND s.project_id=h.project_id WHERE h.project_id=:pid')


def _write(handle, value):
    for chunk in json.JSONEncoder(ensure_ascii=False).iterencode(value):
        # Preserve escapes from older filesystem snapshots without aborting the export.
        handle.write(chunk.encode('utf-8', errors='backslashreplace'))


def _array(handle, values):
    handle.write(b'[')
    for index, value in enumerate(values):
        if index:
            handle.write(b',')
        _write(handle, value)
    handle.write(b']')


def _conversation_copies(db, project_id, *, payload=False):
    source = ConversationImportSource
    query = (select(source).join(Note, (Note.id == source.note_id) &
             (Note.project_id == source.project_id)).where(source.project_id == project_id)
             .order_by(source.id).execution_options(yield_per=1))
    return db.scalars(query if payload else query.options(defer(source.payload)))


def _copy_path(source):
    suffix = Path(source.filename).suffix.lower()
    if suffix not in ('.json', '.md', '.txt'):
        suffix = '.txt'
    return f'sources/originals/{source.note_id}/{source.id}{suffix}'


def _source_copies(handle, db, project_id, *, archived):
    source = ConversationImportSource
    rows = _conversation_copies(db, project_id, payload=not archived)
    handle.write(b'[')
    for index, row in enumerate(rows):
        if index:
            handle.write(b',')
        fields = {column.name: getattr(row, column.name) for column in source.__table__.columns
                  if column.name != 'payload'}
        if archived:
            _write(handle, {**fields, 'archive_path': _copy_path(row)})
            continue
        handle.write(b'{"payload_base64":"')
        # Divisible by three, so only the last chunk can contain base64 padding.
        size = 48 * 1024
        for offset in range(0, len(row.payload), size):
            handle.write(b64encode(row.payload[offset:offset + size]))
        handle.write(b'"')
        for name, value in fields.items():
            handle.write(b',')
            _write(handle, name)
            handle.write(b':')
            _write(handle, value)
        handle.write(b'}')
    handle.write(b']')


class _ExportResponse(StreamingResponse):
    def __init__(self, handle, headers=None, media_type='application/json'):
        self._handle = handle
        size = handle.tell()
        handle.seek(0)
        super().__init__(self._chunks(), media_type=media_type,
                         headers={'Content-Length': str(size), **(headers or {})},
                         background=BackgroundTask(handle.close))

    def _chunks(self):
        while chunk := self._handle.read(_BLOCK_BYTES):
            yield chunk

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # ASGI 2.4 can raise on send before background tasks run.
            self._handle.close()


def audit_response(db, project_id, offset=0, limit=None):
    require_project(db, project_id)
    handle = tempfile.TemporaryFile(mode='w+b')
    try:
        query = select(AuditEvent).where(AuditEvent.project_id == project_id).order_by(AuditEvent.id).offset(offset)
        if limit is not None:
            query = query.limit(limit)
        _array(handle, (serialize(row) for row in db.scalars(query.execution_options(yield_per=1))))
        return _ExportResponse(handle)
    except BaseException:
        handle.close()
        raise


@contextmanager
def _project_snapshot(db, project_id):
    # SQLite's legacy driver otherwise does not BEGIN on SELECT.
    connection = db.connection()
    owns_snapshot = not connection.connection.driver_connection.in_transaction
    if owns_snapshot:
        connection.exec_driver_sql('BEGIN')
    try:
        yield require_project(db, project_id)
    finally:
        # Slow downloads must not retain the SQLite read lock. Never end a
        # caller-owned transaction.
        if owns_snapshot:
            db.rollback()


def _write_project_json(handle, db, project, *, saved=None, source_paths=None):
    from .integrations.codex.revisions import REVISION_KINDS, active_parameter_revision, active_revision_id

    project_id = project.id
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
    _source_copies(handle, db, project_id, archived=source_paths is not None)
    for key, model in _SINGLETONS:
        handle.write(b',')
        _write(handle, key)
        handle.write(b':')
        if saved is not None and key in saved:
            value = saved[key]
            if key in ('knowledge', 'scheme') and value is not None:
                value = {'project_id': project_id, 'snapshot': value}
        else:
            row = db.get(model, project_id)
            value = serialize(row) if row is not None else None
        _write(handle, value)
    for key, query in [
        ('history_turns', f'SELECT h.* {_HISTORY_ROWS} ORDER BY h.id'),
        ('history_index', 'SELECT * FROM codex_history_index WHERE project_id=:pid'),
    ]:
        handle.write(b',')
        _write(handle, key)
        handle.write(b':')
        rows = db.execute(text(query), {'pid': project_id}).mappings()
        _array(handle, (dict(row) for row in rows))
    handle.write(b',"parameters":')
    parameters = (saved['parameters'] if saved is not None else
                  db.scalar(active_parameter_revision(project_id, GenerationRevision.snapshot)))
    _write(handle, parameters)
    handle.write(b',"active_generation_revisions":')
    _write(handle, {kind: active_revision_id(db, project_id, kind) for kind in REVISION_KINDS})
    if source_paths is not None:
        handle.write(b',"source_paths":')
        _write(handle, source_paths)
        handle.write(b',"generation_attempts":')
        attempts = db.scalars(select(ProjectGenerationAttempt).where(
            ProjectGenerationAttempt.project_id == project_id).order_by(ProjectGenerationAttempt.kind))
        _array(handle, (serialize(row) for row in attempts))
    handle.write(b'}')


def project_export_response(db, project_id):
    handle = tempfile.TemporaryFile(mode='w+b')
    try:
        with _project_snapshot(db, project_id) as project:
            _write_project_json(handle, db, project)
        return _ExportResponse(handle)
    except BaseException:
        handle.close()
        raise


def _saved_results(db, project_id):
    from .integrations.codex.knowledge import read_knowledge

    result = {}
    for key, model in _SINGLETONS:
        if key == 'knowledge':
            result[key] = read_knowledge(db, project_id)
        elif key != 'ai_instructions':
            row = db.get(model, project_id)
            result[key] = (row.snapshot if key == 'scheme' else serialize(row)) if row is not None else None
    from .integrations.codex.revisions import active_parameter_revision
    result['parameters'] = db.scalar(active_parameter_revision(project_id, GenerationRevision.snapshot))
    return result


def _write_blob(archive, connection, member, table, column, row_id):
    with connection.blobopen(table, column, row_id, readonly=True) as source:
        with archive.open(member, 'w') as out:
            while chunk := source.read(_BLOCK_BYTES):
                out.write(chunk)


def _archive_sources(archive, db, project_id):
    from .integrations.codex.history_search import _SHARE_REFERENCE
    from .integrations.codex.knowledge_sources import CONVERSATION_NOTE_SQL

    paths = {'notes': {}, 'sessions': {}, 'copies': {}, 'uploads': {}}
    connection = db.connection().connection.driver_connection
    for source in _conversation_copies(db, project_id):
        member = _copy_path(source)
        _write_blob(archive, connection, member, 'conversation_import_sources', 'payload', source.id)
        paths['copies'].setdefault(source.note_id, []).append({
            'path': member, 'filename': source.filename, 'content_hash': source.content_hash,
        })

    query = text(f"SELECT id FROM notes WHERE project_id=:pid AND kind='document' "
                 f"AND ({CONVERSATION_NOTE_SQL} OR lower(reference) GLOB :share) ORDER BY id")
    for note_id, in db.execute(query, {'pid': project_id, 'share': _SHARE_REFERENCE}):
        member = f'sources/conversations/{note_id}.txt'
        _write_blob(archive, connection, member, 'notes', 'content', note_id)
        paths['notes'][note_id] = member

    rows = db.execute(text(f'SELECT h.session_id,s.title,h.turn_id,h.status,h.messages,h.content '
                           f'{_HISTORY_ROWS} ORDER BY h.session_id,h.id'), {'pid': project_id})
    for session_id, turns in groupby(rows, key=lambda row: row.session_id):
        first = next(turns)
        member = f'sources/codex/{session_id}.txt'
        with BufferedWriter(archive.open(member, 'w', force_zip64=True)) as out:
            out.write(f'{first.title}\n\n'.encode('utf-8', errors='backslashreplace'))
            _write_history_turn(out, first)
            for row in turns:
                _write_history_turn(out, row)
        paths['sessions'][session_id] = member

    paths['uploads'] = _archive_uploads(archive, db, project_id)
    return paths


def _write_history_turn(out, row):
    out.write(f'Turn {row.turn_id} ({row.status})\n\n'.encode('utf-8', errors='backslashreplace'))
    try:
        messages = json.loads(row.messages)
    except (ValueError, RecursionError):
        messages = None
    if not isinstance(messages, list):
        out.write(f'{row.content}\n\n'.encode('utf-8', errors='backslashreplace'))
        return
    for message in messages:
        if isinstance(message, dict) and isinstance(message.get('text'), str):
            speaker = message.get('role') or 'Message'
            out.write(f'{speaker}:\n{message["text"]}\n\n'.encode('utf-8', errors='backslashreplace'))


def _archive_uploads(archive, db, project_id):
    # The importer retains the original UTF-8 text unchanged. Use that immutable
    # copy so a missing or changed file cannot affect the exported original.
    reference = AuditEvent.after['reference'].as_string()
    original = AuditEvent.after['content'].as_string().cast(LargeBinary)
    rows = db.execute(select(Note.id, reference.cast(LargeBinary), original).join(
        AuditEvent, (AuditEvent.project_id == Note.project_id) & (AuditEvent.entity_id == Note.id)
        & (AuditEvent.after['created_at'].as_string() == Note.created_at)).where(
            Note.project_id == project_id, AuditEvent.entity_type == 'notes', AuditEvent.action == 'imported',
            AuditEvent.after['kind'].as_string() == 'document',
            func.json_type(AuditEvent.after, '$.content') == 'text',
            func.length(reference).in_((35, 36)), func.length(original) <= _MAX_UPLOAD_BYTES,
        ).order_by(Note.id, AuditEvent.id).execution_options(yield_per=1))
    files = {}
    for note_id, raw_reference, content in rows:
        if note_id in files or b'\x00' in content:
            continue
        try:
            filename = raw_reference.decode('ascii')
            content.decode('utf-8')
        except UnicodeError:
            continue
        if _UPLOAD_NAME.fullmatch(filename):
            member = f'sources/documents/{note_id}{Path(filename).suffix}'
            archive.writestr(member, content)
            files[note_id] = member
    return files


def project_archive_response(db, project_id):
    from .project_export_markdown import write_project_markdown

    handle = tempfile.TemporaryFile(mode='w+b')
    try:
        with _project_snapshot(db, project_id) as project:
            filename = re.sub(r'[^A-Za-z0-9_-]+', '-', project.name).strip('-')[:70] or f'project-{project_id}'
            with ZipFile(handle, 'w', compression=ZIP_DEFLATED, compresslevel=3) as archive:
                saved = _saved_results(db, project_id)
                paths = _archive_sources(archive, db, project_id)
                with BufferedWriter(archive.open('project.json', 'w', force_zip64=True)) as out:
                    _write_project_json(out, db, project, saved=saved, source_paths=paths)
                write_project_markdown(archive, db, project, saved, paths)
        return _ExportResponse(handle, {'Content-Disposition': f'attachment; filename="{filename}-project.zip"'},
                               media_type='application/zip')
    except BaseException:
        handle.close()
        raise
