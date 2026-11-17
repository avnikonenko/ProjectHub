"""Small durable execution outcomes; never duplicates model output or prompts."""
import re
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from ...models import (GenerationRevision, ProjectBrief, ProjectGenerationAttempt,
                       ProjectKnowledge, ProjectQA, ProjectScheme, utcnow)
from ...services import require_project

KINDS = ('brief', 'knowledge', 'scheme', 'qa', 'parameters')
_STATES = {'running', 'waiting', 'completed', 'failed', 'cancelled', 'interrupted', 'skipped'}
_TIMEOUT = re.compile(r'timed?\s*out|timeout|deadline|exceeded.{0,50}time\s*budget|did not finish generation within', re.I)
_HISTORY_TIMESTAMP = re.compile(
    r'[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]'
    r'(?:\.[0-9]{1,9})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?')


def _metadata(row):
    if row is None:
        return None
    result = {name: getattr(row, name) for name in
              ('project_id', 'kind', 'started_at', 'finished_at', 'state', 'error', 'saves_result')}
    result['error_category'] = ('timeout' if row.state == 'failed' and _TIMEOUT.search(row.error or '')
                                else 'interrupted' if row.state == 'interrupted' else None)
    return result


def _validate(kind, started_at):
    if (not isinstance(kind, str) or not kind.strip() or len(kind) > 32
            or not isinstance(started_at, str) or not started_at.strip() or len(started_at) > 80):
        raise ValueError('Generation attempt requires a view and start identity.')


def start(engine, pid, kind, started_at, *, saves_result=True):
    """Replace an older outcome without allowing a stale run to replace a retry."""
    _validate(kind, started_at)
    with Session(engine) as db:
        require_project(db, pid)
        statement = insert(ProjectGenerationAttempt).values(
            project_id=pid, kind=kind, started_at=started_at, finished_at=None,
            state='running', error=None, saves_result=bool(saves_result))
        db.execute(statement.on_conflict_do_update(
            index_elements=['project_id', 'kind'],
            set_=dict(started_at=started_at, finished_at=None, state='running', error=None,
                      saves_result=bool(saves_result)),
            where=ProjectGenerationAttempt.started_at <= started_at))
        db.commit()


def finish(engine, pid, kind, started_at, *, state, error=None, finished_at=None, db=None):
    """CAS the run identity; optionally stage the outcome in its artifact transaction."""
    _validate(kind, started_at)
    if state not in _STATES or state in {'running', 'waiting'}:
        raise ValueError('Generation attempt requires a terminal outcome.')
    values = dict(state=state, error=str(error)[:4000] if error else None,
                  finished_at=finished_at or utcnow())
    def apply(session):
        require_project(session, pid)
        session.execute(update(ProjectGenerationAttempt).where(
            ProjectGenerationAttempt.project_id == pid, ProjectGenerationAttempt.kind == kind,
            ProjectGenerationAttempt.started_at == started_at,
            ProjectGenerationAttempt.state.in_(('running', 'waiting'))).values(**values))
    if db is not None:
        binding = db.get_bind()
        if binding is not engine and getattr(binding, 'engine', None) is not engine:
            raise ValueError('Attempt transaction belongs to another database.')
        apply(db)
    else:
        with Session(engine) as session:
            apply(session)
            session.commit()


def read_latest(db, pid, kind):
    require_project(db, pid)
    return _metadata(db.get(ProjectGenerationAttempt, (pid, kind), populate_existing=True))


def interrupt_running(engine):
    """A restarted process cannot resume old in-memory model requests or queues."""
    with Session(engine) as db:
        db.execute(update(ProjectGenerationAttempt).where(
            ProjectGenerationAttempt.state.in_(('running', 'waiting'))).values(
                state='interrupted', finished_at=utcnow(),
                error='ProjectHub restarted before this job completed. Previous saved results and completed caches are preserved.'))
        db.commit()


def queue_dependency(engine, pid, kind='scheme', dependency='knowledge'):
    """Record a requested follow-up without replacing an active or newer job."""
    if kind != 'scheme' or dependency != 'knowledge':
        raise ValueError('Unsupported generation dependency.')
    token = utcnow()
    with Session(engine) as db:
        require_project(db, pid)
        statement = insert(ProjectGenerationAttempt).values(
            project_id=pid, kind=kind, started_at=token, finished_at=None, state='waiting',
            error=None, saves_result=True)
        outcome = db.execute(statement.on_conflict_do_update(
            index_elements=['project_id', 'kind'],
            set_=dict(started_at=token, finished_at=None, state='waiting', error=None, saves_result=True),
            where=~ProjectGenerationAttempt.state.in_(('running', 'waiting'))))
        db.commit()
        return token if outcome.rowcount else None


def saved_times(db, pid, *, with_presence=False):
    """Read scalar timestamps only, not potentially megabyte-sized saved records."""
    require_project(db, pid)
    queries = {
        'brief': select(ProjectBrief.generated_at).where(ProjectBrief.project_id == pid),
        'knowledge': select(ProjectKnowledge.snapshot['generated_at'].as_string()).where(ProjectKnowledge.project_id == pid),
        'scheme': select(ProjectScheme.snapshot['generated_at'].as_string()).where(ProjectScheme.project_id == pid),
        'qa': select(ProjectQA.generated_at).where(ProjectQA.project_id == pid),
        'parameters': select(GenerationRevision.generated_at).where(
            GenerationRevision.project_id == pid, GenerationRevision.kind == 'parameters').order_by(GenerationRevision.id.desc()).limit(1),
    }
    times, present = {}, {}
    for kind, query in queries.items():
        row = db.execute(query).first()
        times[kind], present[kind] = (row[0] if row else None), row is not None
    return (times, present) if with_presence else times


def _legacy_knowledge_time(db, pid):
    """Read identity scalars for a legacy result, never its large source bodies.

    Existence in revision history is separate from current Knowledge. It neither
    activates old evidence nor establishes that it could safely be saved today.
    Full validation remains the historical result reader's responsibility.
    """
    snapshot = GenerationRevision.snapshot
    row = db.execute(select(
        GenerationRevision.generated_at.label('stored_at'),
        func.json_extract(snapshot, '$.generated_at').label('snapshot_at'),
        func.json_extract(snapshot, '$.result.publication_id').label('publication'),
        func.json_extract(snapshot, '$.result.knowledge.project_id').label('project_id'),
        func.json_extract(snapshot, '$.result.knowledge.publication_id').label('knowledge_publication'),
        func.json_extract(snapshot, '$.result.knowledge.generated_at').label('knowledge_at'),
        func.json_extract(snapshot, '$.result.brief.project_state.publication_id').label('brief_publication'),
        func.json_extract(snapshot, '$.result.brief.generated_at').label('brief_at'),
        func.json_type(snapshot, '$.result').label('result_type'),
        func.json_type(snapshot, '$.result.knowledge').label('knowledge_type'),
        func.json_type(snapshot, '$.result.brief').label('brief_type'),
        func.json_type(snapshot, '$.result.published').label('published_type'),
        func.json_type(snapshot, '$.result.knowledge.project_id').label('project_type'),
        func.json_type(snapshot, '$.result.baseline_brief').label('baseline_type'),
        func.json_type(snapshot, '$.result.diagnostics').label('diagnostics_type'),
    ).where(GenerationRevision.project_id == pid, GenerationRevision.kind == 'knowledge_preview')
        .order_by(GenerationRevision.id.desc()).limit(1)).mappings().first()
    if row is None:
        return None
    timestamp, publication = row['stored_at'], row['publication']
    if (row['result_type'] != 'object' or row['knowledge_type'] != 'object'
            or row['brief_type'] != 'object' or row['published_type'] != 'false'
            or row['project_type'] != 'integer' or row['project_id'] != pid
            or row['baseline_type'] is None or row['diagnostics_type'] is None
            or not isinstance(timestamp, str) or not timestamp.strip() or len(timestamp) > 100
            or not isinstance(publication, str) or not publication.strip() or len(publication) > 200
            or row['knowledge_publication'] != publication or row['brief_publication'] != publication
            or any(row[key] != timestamp for key in ('snapshot_at', 'knowledge_at', 'brief_at'))):
        return None
    # Summary dates must also be readable by browser Date.parse. Restrict the
    # legacy scalar to the extended ISO shape emitted by this application.
    if not _HISTORY_TIMESTAMP.fullmatch(timestamp):
        return None
    try:
        datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    except ValueError:
        return None
    return timestamp


def generation_summary(db, pid, *, live=None):
    # SQLite's legacy driver mode does not begin a physical transaction for
    # SELECTs. Keep artifact timestamps, archive metadata and attempt outcomes
    # on one snapshot so an atomic save cannot look like a completed job with
    # no result while this lightweight summary is being assembled. A scoped
    # savepoint ends a new read snapshot without ending a caller's transaction;
    # begin_nested also flushes caller changes before the savepoint is created.
    connection = db.connection().connection.driver_connection
    if connection.in_transaction:
        return _generation_summary(db, pid, live=live)
    with db.begin_nested():
        return _generation_summary(db, pid, live=live)


def _generation_summary(db, pid, *, live=None):
    require_project(db, pid)
    saved, present = saved_times(db, pid, with_presence=True)
    history_at = None if present['knowledge'] else _legacy_knowledge_time(db, pid)
    views = []
    for kind in KINDS:
        attempt = read_latest(db, pid, kind)
        status = (live or {}).get(kind, {})
        started = status.get('started_at')
        if (status.get('state') in {'completed', 'failed', 'cancelled'} and isinstance(started, str)
                and (attempt is None or started > attempt['started_at']
                     or (started == attempt['started_at'] and attempt['state'] in {'running', 'waiting'}))):
            # Known current-process outcomes remain visible even if persistence
            # failed. Missing history after a restart is never reconstructed.
            attempt = {'project_id': pid, 'kind': kind, 'started_at': started,
                       'finished_at': status.get('finished_at'), 'state': status['state'],
                       'error': status.get('error'), 'saves_result': status.get('saves_result', True),
                       'error_category': 'timeout' if status['state'] == 'failed' and _TIMEOUT.search(status.get('error') or '') else None}
        # A running in-memory job is authoritative, including compatibility
        # callers which do not persist attempts yet. Never infer success from it.
        if status.get('state') == 'running':
            state = 'running'
        elif attempt is None or (attempt['state'] == 'completed'):
            state = ('saved' if present[kind] else 'earlier_result'
                     if kind == 'knowledge' and history_at else 'not_generated')
        elif attempt['state'] == 'failed' and attempt['error_category'] == 'timeout':
            state = 'timed_out'
        else:
            state = attempt['state']
        views.append({'kind': kind, 'state': state, 'saved_at': saved[kind],
                      'history_at': history_at if kind == 'knowledge' else None,
                      'latest_attempt': attempt,
                      'showing_previous': bool(present[kind] and state != 'saved'),
                      'retryable': state in {'not_generated', 'earlier_result', 'failed', 'timed_out', 'cancelled', 'interrupted', 'skipped'}})
    return {'project_id': pid, 'views': views}
