"""Small durable execution outcomes; never duplicates model output or prompts."""
import re

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from ...models import (GenerationRevision, ProjectBrief, ProjectGenerationAttempt,
                       ProjectKnowledge, ProjectQA, ProjectScheme, utcnow)
from ...services import require_project

KINDS = ('brief', 'knowledge', 'scheme', 'qa', 'parameters')
_STATES = {'running', 'waiting', 'completed', 'failed', 'cancelled', 'interrupted', 'skipped'}
_TIMEOUT = re.compile(r'timed?\s*out|timeout|deadline|exceeded.{0,50}time\s*budget|did not finish generation within', re.I)


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


def generation_summary(db, pid, *, live=None):
    require_project(db, pid)
    saved, present = saved_times(db, pid, with_presence=True)
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
            state = 'saved' if present[kind] else 'not_generated'
        elif attempt['state'] == 'failed' and attempt['error_category'] == 'timeout':
            state = 'timed_out'
        else:
            state = attempt['state']
        views.append({'kind': kind, 'state': state, 'saved_at': saved[kind],
                      'latest_attempt': attempt,
                      'showing_previous': bool(present[kind] and state != 'saved'),
                      'retryable': state in {'not_generated', 'failed', 'timed_out', 'cancelled', 'interrupted', 'skipped'}})
    return {'project_id': pid, 'views': views}
