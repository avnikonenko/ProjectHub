"""Per-project snapshots for the active generation and each kind's last result."""
import threading

from ...models import utcnow

_lock = threading.Lock()
_active = {}
_snapshots = {}


def _idle():
    return {'state': 'idle', 'stage': '', 'sessions': 0, 'turns': 0, 'batches': 0, 'error': None}


def begin(project_id, kind):
    # The caller holds the shared generation lock through finish().
    with _lock:
        if project_id in _active:
            raise RuntimeError('Generation progress is already active for this project.')
        _active[project_id] = kind
        _snapshots[project_id, kind] = {**_idle(), 'state': 'running', 'stage': 'Starting generation', 'started_at': utcnow()}


def update(project_id, **fields):
    with _lock:
        kind = _active.get(project_id)
        if kind is not None:
            snapshot = _snapshots[project_id, kind]
            for key in ('stage', 'sessions', 'turns', 'batches'):
                if key in fields:
                    snapshot[key] = fields[key]


def finish(project_id, error=None):
    with _lock:
        kind = _active.pop(project_id, None)
        if kind is not None:
            snapshot = _snapshots[project_id, kind]
            snapshot.update(state='completed' if error is None else 'failed',
                            stage='Completed' if error is None else 'Failed',
                            error=None if error is None else str(error))


def read(project_id, kind):
    with _lock:
        return dict(_snapshots.get((project_id, kind), _idle()))
