"""Shared provider-reported token accounting for an AI generation action."""
from contextlib import contextmanager
from contextvars import ContextVar
import threading
from time import monotonic
import weakref

from .protocol import CodexError

_current = ContextVar('projecthub_generation_token_budget', default=None)
_action_lock = threading.Lock()
_actions = {}
_ACTION_RETENTION_SECONDS = 7200
_MAX_ACTIONS = 1024


class GenerationTokenLimitExceeded(CodexError):
    def __init__(self, limit):
        super().__init__(f'AI generation reached its limit of {limit:,} reported tokens.')


class TokenBudget:
    def __init__(self, limit):
        self.limit = limit
        self._lock = threading.Lock()
        self._reached = threading.Event()
        self._total = 0
        self._reported = 0
        self._requests = 0
        self._users = 0
        self._last_used = monotonic()

    @property
    def reached(self):
        return self._reached.is_set()

    def check(self):
        if self.reached:
            raise GenerationTokenLimitExceeded(self.limit)

    def _model_started(self):
        with self._lock:
            self.check()
            self._requests += 1
            self._last_used = monotonic()

    def _record_total(self, previous, current):
        """Add the delta of an already admitted request's cumulative usage."""
        with self._lock:
            if previous is None:
                self._reported += 1
                self._total += current
            else:
                self._total += current - previous
            if self.limit and self._total >= self.limit:
                self._reached.set()
            self._last_used = monotonic()

    def status(self):
        with self._lock:
            return {
                'limit': self.limit,
                'total_tokens': self._total if self._reported else None,
                'reported_requests': self._reported,
                'model_requests': self._requests,
                'reached': self.reached,
                'active_jobs': self._users,
            }

    def _expired(self, now):
        with self._lock:
            return not self._users and now - self._last_used >= _ACTION_RETENTION_SECONDS


@contextmanager
def use_token_budget(budget):
    if budget is not None:
        with budget._lock:
            budget._users += 1
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)
        if budget is not None:
            with budget._lock:
                budget._users -= 1
                budget._last_used = monotonic()


def current_token_budget():
    return _current.get()


def _action_key(engine, project_id, action_id):
    return weakref.ref(engine) if engine is not None else None, project_id, action_id


def action_budget(project_id, action_id, limit, *, engine=None):
    """Use the action's original allowance, including for late sibling jobs."""
    key = _action_key(engine, project_id, action_id)
    with _action_lock:
        now = monotonic()
        for expired in [stored for stored, budget in _actions.items()
                        if (stored[0] is not None and stored[0]() is None) or budget._expired(now)]:
            del _actions[expired]
        if key not in _actions:
            if len(_actions) >= _MAX_ACTIONS:
                raise CodexError('ProjectHub has too many recent generation actions to track another token allowance.')
            _actions[key] = TokenBudget(limit)
        return _actions[key]


def action_budget_status(project_id, action_id, *, engine=None):
    with _action_lock:
        budget = _actions.get(_action_key(engine, project_id, action_id))
        if budget is None or budget._expired(monotonic()):
            return None
        return budget.status()
