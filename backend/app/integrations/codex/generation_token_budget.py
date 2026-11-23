"""Shared provider-reported token accounting for an AI generation action."""
from contextlib import ExitStack, contextmanager, nullcontext
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
        self._retained = False
        self._last_used = monotonic()

    @property
    def reached(self):
        return self._reached.is_set()

    def check(self):
        if self.reached:
            raise GenerationTokenLimitExceeded(self.limit)

    @contextmanager
    def track_job(self):
        """Keep the action active while a job runs or waits to start."""
        with self._lock:
            self._users += 1
        try:
            yield
        finally:
            with self._lock:
                self._users -= 1
                self._last_used = monotonic()

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

    def _discard_unadmitted(self, retain):
        with self._lock:
            self._retained |= retain
            return not (self._retained or self._users or self._requests)


@contextmanager
def use_token_budget(budget):
    with budget.track_job() if budget is not None else nullcontext():
        token = _current.set(budget)
        try:
            yield budget
        finally:
            _current.reset(token)


def current_token_budget():
    return _current.get()


def _action_key(engine, project_id, action_id):
    return weakref.ref(engine) if engine is not None else None, project_id, action_id


@contextmanager
def use_action_budget(project_id, action_id, limit, *, engine=None, discard_on=()):
    """Reserve the action's original allowance, including for late sibling jobs."""
    key = _action_key(engine, project_id, action_id)
    with ExitStack() as scope:
        with _action_lock:
            now = monotonic()
            for expired in [stored for stored, budget in _actions.items()
                            if (stored[0] is not None and stored[0]() is None) or budget._expired(now)]:
                del _actions[expired]
            if key not in _actions:
                if len(_actions) >= _MAX_ACTIONS:
                    raise CodexError('ProjectHub has too many recent generation actions to track another token allowance.')
                _actions[key] = TokenBudget(limit)
            budget = _actions[key]
            scope.enter_context(use_token_budget(budget))
        retain = True
        try:
            yield budget
        except discard_on:
            retain = False
            raise
        finally:
            with _action_lock:
                # Release the holder and decide whether to discard the entry
                # before another request can claim the same action.
                scope.close()
                if budget._discard_unadmitted(retain):
                    del _actions[key]


def action_budget_status(project_id, action_id, *, engine=None):
    with _action_lock:
        budget = _actions.get(_action_key(engine, project_id, action_id))
        if budget is None or budget._expired(monotonic()):
            return None
        return budget.status()
