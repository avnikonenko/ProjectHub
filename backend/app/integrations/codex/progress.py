"""Project-scoped generation progress and cooperative cancellation."""
from contextvars import ContextVar
from contextlib import contextmanager
import threading
import time
import os
import logging

from ...models import utcnow
from .protocol import CodexError

_lock = threading.Lock()
_active = {}
_snapshots = {}
_events = {}
_clocks = {}
_stage_clocks = {}
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


def _check_budget(project_id):
    clock = _clocks.get(project_id)
    if clock and time.monotonic() - clock[0] >= clock[1]:
        raise CodexError("AI generation exceeded its overall time budget. The previous saved result and completed batch caches are preserved.")


def begin(project_id, kind):
    budget = _budget()
    # The caller holds the shared generation lock through finish().
    with _lock:
        if project_id in _active:
            raise RuntimeError('Generation progress is already active for this project.')
        event = threading.Event()
        _clocks[project_id] = (time.monotonic(), budget)
        _stage_clocks[project_id] = time.monotonic()
        _active[project_id] = kind
        _events[project_id] = event
        _snapshots[project_id, kind] = {**_idle(), 'state': 'running', 'stage': 'Starting generation', 'started_at': utcnow(), 'budget_seconds': budget}
        _current.set((project_id, event))


def current_cancel_event():
    current = _current.get()
    return current[1] if current is not None else None


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
        _check_budget(current[0])
    abort = current_abort_event()
    if abort is not None and abort.is_set():
        raise CodexError('Another extraction batch failed.')


def cancel(project_id, kind, started_at):
    """Cancel only the exact generation observed by the caller."""
    with _lock:
        snapshot = _snapshots.get((project_id, kind))
        if (_active.get(project_id) != kind or snapshot is None
                or snapshot.get('started_at') != started_at or snapshot['state'] != 'running'):
            raise CodexError('This AI generation is no longer running. Refresh its status.')
        _events[project_id].set()
        _record_stage(project_id, snapshot)
        snapshot.update(stage='Cancelling', cancel_requested=True)
        return {**snapshot, 'stage_seconds': dict(snapshot.get('stage_seconds', {}))}


def _record_stage(project_id, snapshot):
    now = time.monotonic()
    elapsed = max(0, now - _stage_clocks.get(project_id, now))
    name = snapshot.get('stage', '')
    stages = snapshot.setdefault('stage_seconds', {})
    stages[name] = round(stages.get(name, 0) + elapsed, 3)
    _stage_clocks[project_id] = now
    log.info('AI stage project=%s kind=%s stage=%s seconds=%.3f',
             project_id, _active.get(project_id), name, elapsed)


def update(project_id, **fields):
    with _lock:
        kind = _active.get(project_id)
        if kind is not None:
            if _events[project_id].is_set():
                raise GenerationCancelled()
            _check_budget(project_id)
            snapshot = _snapshots[project_id, kind]
            if 'stage' in fields and fields['stage'] != snapshot['stage']:
                _record_stage(project_id, snapshot)
            for key in ('stage', 'sessions', 'turns', 'batches', 'batch_total', 'batch_completed',
                        'batch_started_at', 'last_batch_seconds', 'batch_phase', 'cache_hits'):
                if key in fields:
                    snapshot[key] = fields[key]


def _finish_locked(project_id, error=None):
    kind = _active.get(project_id)
    if kind is not None:
        snapshot = _snapshots[project_id, kind]
        _record_stage(project_id, snapshot)
        snapshot['elapsed_seconds'] = round(max(0, time.monotonic() - _clocks[project_id][0]), 3)
        _stage_clocks.pop(project_id, None)
    kind = _active.pop(project_id, None)
    event = _events.pop(project_id, None)
    if kind is not None:
        cancelled = event is not None and event.is_set()
        snapshot = _snapshots[project_id, kind]
        snapshot.update(state='cancelled' if cancelled else 'completed' if error is None else 'failed',
                        stage='Cancelled' if cancelled else 'Completed' if error is None else 'Failed',
                        error=None if cancelled or error is None else str(error))
    current = _current.get()
    if current is not None and current[0] == project_id:
        _current.set(None)


def finish(project_id, error=None):
    with _lock:
        _finish_locked(project_id, error)


def complete(project_id, save):
    """Atomically accept a successful result or an earlier cancellation.

    The save callback must not call back into progress. A cancellation cannot
    report success between the final cancellation check and the database commit.
    """
    with _lock:
        if project_id not in _active:
            raise CodexError('This AI generation is no longer running.')
        if _events[project_id].is_set():
            raise GenerationCancelled()
        _check_budget(project_id)
        result = save()
        _finish_locked(project_id)
        return result


def read(project_id, kind):
    with _lock:
        result = dict(_snapshots.get((project_id, kind), _idle()))
        if 'stage_seconds' in result:
            result['stage_seconds'] = dict(result['stage_seconds'])
        if _active.get(project_id) == kind:
            result['elapsed_seconds'] = round(max(0, time.monotonic() - _clocks[project_id][0]), 3)
        return result
