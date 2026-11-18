"""Per-project/view generation progress and cooperative cancellation."""
from contextvars import ContextVar
from contextlib import contextmanager
from copy import deepcopy
import threading
import time
import os
import logging
import weakref

from sqlalchemy.orm import Session

from ...models import utcnow
from .protocol import CodexError

_lock = threading.Lock()
# Keys include the view; event identity additionally identifies a particular run.
_active = {}
_snapshots = {}
_events = {}
_clocks = {}
_stage_clocks = {}
_run_locks = {}
# Weak references avoid retaining disposed test/application engines with snapshots.
_stores = {}
log = logging.getLogger('uvicorn.error')
_current = ContextVar('projecthub_active_generation', default=None)
_abort = ContextVar('projecthub_extraction_abort', default=None)


class GenerationCancelled(CodexError):
    def __init__(self):
        super().__init__('AI generation cancelled. The previous saved result is unchanged.')


def _idle():
    return {'state': 'idle', 'stage': '', 'sessions': 0, 'turns': 0, 'batches': 0, 'error': None}


def _budget():
    try:
        value = int(os.environ.get("PROJECTHUB_GENERATION_BUDGET", "900"))
    except ValueError as exc:
        raise CodexError("Generation budget must be an integer from 30 to 7200 seconds.") from exc
    if not 30 <= value <= 7200:
        raise CodexError("Generation budget must be from 30 to 7200 seconds.")
    return value


def _owned_key(project_id):
    """Resolve only this request/worker's run, never another active sibling."""
    current = _current.get()
    if current is None or current[0] != project_id:
        return None
    key = current[:2]
    return key if _active.get(key) is current[2] else None


def _check_budget(key):
    clock = _clocks.get(key)
    if clock and time.monotonic() - clock[0] >= clock[1]:
        raise CodexError("AI generation exceeded its overall time budget. The previous saved result and completed batch caches are preserved.")


def _owned_run_lock(project_id):
    with _lock:
        key = _owned_key(project_id)
        return _run_locks.get(key) if key is not None else None


def begin(project_id, kind, engine=None, *, saves_result=True, manager=None):
    from .generation_attempts import settings_for, start
    budget = _budget()
    settings = settings_for(manager, engine) if manager is not None else None
    key = (project_id, kind)
    # The caller holds a lease for this project/view through finish().
    with _lock:
        if key in _active:
            raise RuntimeError('Generation progress is already active for this project/view.')
        event = threading.Event()
        _clocks[key] = (time.monotonic(), budget)
        _stage_clocks[key] = time.monotonic()
        _active[key] = event
        _events[key] = event
        _run_locks[key] = threading.Lock()
        _snapshots[key] = {**_idle(), 'state': 'running', 'stage': 'Starting generation',
                           'started_at': utcnow(), 'budget_seconds': budget}
        if settings is not None:
            _snapshots[key]['settings'] = settings
        _current.set((project_id, kind, event))
        _stores[key] = weakref.ref(engine) if engine is not None else None
    if engine is not None:
        try:
            start(engine, project_id, kind, _snapshots[key]['started_at'], saves_result=saves_result,
                  settings=settings)
        except Exception as exc:
            with _lock:
                if _active.get(key) is event:
                    _finish_locked(project_id, 'Could not save generation attempt status. No generation was started.')
            raise CodexError('Could not save generation attempt status. No generation was started.') from exc


def record_settings(project_id, manager):
    """Capture an effective profile change before the first generation request."""
    from .generation_attempts import settings_for, update_settings
    run_lock = _owned_run_lock(project_id)
    if run_lock is None:
        return
    with run_lock:
        with _lock:
            key = _owned_key(project_id)
            if key is None:
                return
            reference = _stores.get(key)
            engine = reference() if reference is not None else None
            snapshot = _snapshots[key]
        settings = settings_for(manager, engine)
        if snapshot.get('settings') == settings:
            return
        if engine is not None:
            update_settings(engine, project_id, key[1], snapshot['started_at'], settings)
        with _lock:
            snapshot['settings'] = settings


def current_cancel_event():
    current = _current.get()
    return current[2] if current is not None else None


def current_abort_event():
    return _abort.get()


@contextmanager
def abort_on(event):
    """Stop sibling work without misrepresenting a failure as user cancellation."""
    token = _abort.set(event)
    try:
        yield
    finally:
        _abort.reset(token)


def check_cancelled():
    event = current_cancel_event()
    if event is not None and event.is_set():
        raise GenerationCancelled()
    current = _current.get()
    if current is not None:
        with _lock:
            key = _owned_key(current[0])
            if key is None:
                raise CodexError('This AI generation is no longer running.')
            _check_budget(key)
    abort = current_abort_event()
    if abort is not None and abort.is_set():
        raise CodexError('Another extraction batch failed.')


def cancel(project_id, kind, started_at):
    """Cancel only the exact project/view generation observed by the caller."""
    key = (project_id, kind)
    with _lock:
        event = _active.get(key)
        run_lock = _run_locks.get(key)
        if event is None or run_lock is None:
            raise CodexError('This AI generation is no longer running. Refresh its status.')
    with run_lock:
        with _lock:
            snapshot = _snapshots.get(key)
            if (_active.get(key) is not event or snapshot is None
                    or snapshot.get('started_at') != started_at or snapshot['state'] != 'running'):
                raise CodexError('This AI generation is no longer running. Refresh its status.')
            _events[key].set()
            _record_stage(key, snapshot)
            snapshot.update(stage='Cancelling', cancel_requested=True)
            return deepcopy(snapshot)


def record_diagnostics(project_id, diagnostics):
    """Retain trusted diagnostics for this run, including after cancellation."""
    with _lock:
        key = _owned_key(project_id)
        if key is not None:
            _snapshots[key]['diagnostics'] = deepcopy(diagnostics)


def _record_stage(key, snapshot):
    now = time.monotonic()
    elapsed = max(0, now - _stage_clocks.get(key, now))
    name = snapshot.get('stage', '')
    stages = snapshot.setdefault('stage_seconds', {})
    stages[name] = round(stages.get(name, 0) + elapsed, 3)
    _stage_clocks[key] = now
    log.info('AI stage project=%s kind=%s stage=%s seconds=%.3f',
             key[0], key[1], name, elapsed)


def update(project_id, **fields):
    with _lock:
        key = _owned_key(project_id)
        if key is not None:
            if _events[key].is_set():
                raise GenerationCancelled()
            _check_budget(key)
            snapshot = _snapshots[key]
            if 'stage' in fields and fields['stage'] != snapshot['stage']:
                _record_stage(key, snapshot)
            for name in ('stage', 'sessions', 'turns', 'batches', 'batch_total', 'batch_completed',
                         'batch_started_at', 'last_batch_seconds', 'batch_phase', 'cache_hits'):
                if name in fields:
                    snapshot[name] = fields[name]


def _finish_locked(project_id, error=None):
    key = _owned_key(project_id)
    if key is not None:
        snapshot = _snapshots[key]
        _record_stage(key, snapshot)
        snapshot['elapsed_seconds'] = round(max(0, time.monotonic() - _clocks[key][0]), 3)
        _stage_clocks.pop(key, None)
        _active.pop(key, None)
        _run_locks.pop(key, None)
        event = _events.pop(key, None)
        cancelled = event is not None and event.is_set()
        snapshot.update(state='cancelled' if cancelled else 'completed' if error is None else 'failed',
                        stage='Cancelled' if cancelled else 'Completed' if error is None else 'Failed',
                        error=None if cancelled or error is None else str(error))
    current = _current.get()
    if current is not None and current[0] == project_id:
        _current.set(None)


def _persistence_record(project_id):
    # Caller owns the run lock; never keep the map mutex during database IO.
    with _lock:
        key = _owned_key(project_id)
        if key is None:
            return None
        reference = _stores.get(key)
        engine = reference() if reference is not None else None
        return key, engine, _snapshots[key]['started_at'], _events[key].is_set()


def _persist_terminal(record, *, error=None, db=None, finished_at=None):
    if record is None or record[1] is None:
        return
    key, engine, started_at, cancelled = record
    from .generation_attempts import finish as finish_attempt
    finish_attempt(engine, key[0], key[1], started_at,
                   state='cancelled' if cancelled else 'failed' if error is not None else 'completed',
                   error=None if cancelled else error, finished_at=finished_at or utcnow(), db=db)


def finish(project_id, error=None):
    run_lock = _owned_run_lock(project_id)
    if run_lock is None:
        with _lock:
            _finish_locked(project_id, error)
    else:
        with run_lock:
            record = _persistence_record(project_id)
            try:
                _persist_terminal(record, error=error)
            finally:
                with _lock:
                    _finish_locked(project_id, error)


def complete(project_id, save, *, db=None):
    """Atomically save this run or accept its earlier cancellation.

    A supplied output Session stages successful attempt status in the SAME
    transaction as the artifact before save() commits. Never hold the global
    progress mutex across database IO; sibling SQLite writers must make progress.
    """
    run_lock = _owned_run_lock(project_id)
    if run_lock is None:
        raise CodexError('This AI generation is no longer running.')
    with run_lock:
        with _lock:
            key = _owned_key(project_id)
            if key is None:
                raise CodexError('This AI generation is no longer running.')
            if _events[key].is_set():
                raise GenerationCancelled()
            _check_budget(key)
        record = _persistence_record(project_id)
        if db is not None:
            _persist_terminal(record, db=db)
        result = save()
        if db is None:
            _persist_terminal(record)
        with _lock:
            _finish_locked(project_id)
        return result


def _stored_progress(engine, project_id, kind):
    from .generation_attempts import read_latest
    with Session(engine) as db:
        attempt = read_latest(db, project_id, kind)
    if attempt is None:
        return _idle()
    state = attempt['state']
    legacy_state = state if state in ('running', 'completed', 'failed', 'cancelled') else 'failed' if state in ('interrupted', 'skipped') else 'idle'
    stages = {'running': 'Generating', 'completed': 'Completed', 'failed': 'Failed',
              'cancelled': 'Cancelled', 'interrupted': 'Interrupted',
              'waiting': 'Waiting for Knowledge', 'skipped': 'Skipped'}
    return {**_idle(), 'state': legacy_state, 'stage': stages.get(state, ''),
            'started_at': attempt['started_at'], 'error': attempt.get('error'),
            'latest_attempt': attempt,
            **({'elapsed_seconds': attempt['elapsed_seconds']} if attempt.get('elapsed_seconds') is not None else {})}

def read(project_id, kind, engine=None):
    key = (project_id, kind)
    with _lock:
        reference = _stores.get(key)
        owner = reference() if reference is not None else None
        # Calls without an engine retain the historical in-memory interface.
        # API reads for a different database must never inherit its snapshots.
        available = key in _snapshots and (engine is None or owner is engine
                     or (key in _active and reference is None))
        result = deepcopy(_snapshots[key]) if available else None
        active = key in _active
        if result is not None and active:
            result['elapsed_seconds'] = round(max(0, time.monotonic() - _clocks[key][0]), 3)
    if engine is None:
        return result if result is not None else _idle()
    stored = _stored_progress(engine, project_id, kind)
    if result is None:
        return stored
    if not active and not stored.get('latest_attempt'):
        # Project deletion cascades its durable attempts. SQLite may reuse that
        # numeric project ID; its new owner must not inherit terminal memory.
        return stored
    if (not active and stored.get('latest_attempt')
            and stored['latest_attempt']['started_at'] > result.get('started_at', '')):
        # A server-owned dependency can be queued after an older live snapshot.
        # That durable attempt is newer than this completed in-memory result.
        return stored
    if 'latest_attempt' in stored:
        result['latest_attempt'] = stored['latest_attempt']
    return result
