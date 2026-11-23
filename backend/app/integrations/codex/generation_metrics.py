"""Best-effort, request-local usage from provider reports.

No prompt, response, account or thread identifiers are exposed by snapshots.
The context is copied to extraction workers; callbacks keep their own recorder
because the App Server reader thread does not inherit the request context.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import threading


_current = ContextVar('projecthub_generation_metrics', default=None)
_FIELDS = {
    'totalTokens': 'total_tokens',
    'inputTokens': 'input_tokens',
    'outputTokens': 'output_tokens',
    'cachedInputTokens': 'cached_input_tokens',
    'reasoningOutputTokens': 'reasoning_output_tokens',
}
_MAX_COUNT = 2 ** 63 - 1


def _usage(params):
    if not isinstance(params, dict):
        return None
    value = params.get('tokenUsage')
    if not isinstance(value, dict) or not isinstance(value.get('total'), dict):
        return None
    values = value['total']
    result = {}
    for original, name in _FIELDS.items():
        if original not in values:
            continue
        count = values[original]
        if type(count) is not int or not 0 <= count <= _MAX_COUNT:
            return None
        result[name] = count
    return result or None


class _ModelRequest:
    def __init__(self, owner, thread_id):
        self._owner = owner
        self._thread_id = thread_id
        self._turn_id = None
        self._pending = {}
        self._usage = None

    def bind_turn(self, turn_id):
        """Resolve notifications emitted before the turn/start acknowledgement."""
        if not isinstance(turn_id, str) or not turn_id:
            return
        with self._owner._lock:
            if self._turn_id is not None:
                return
            self._turn_id = turn_id
            self._update_usage(self._pending.get(turn_id))
            self._pending.clear()

    def record(self, params):
        if not isinstance(params, dict) or params.get('threadId') != self._thread_id:
            return
        turn_id = params.get('turnId')
        if not isinstance(turn_id, str) or not turn_id:
            return
        usage = _usage(params)
        if usage is None:
            return
        with self._owner._lock:
            if self._turn_id is None:
                # Bound untrusted pre-ack turn IDs without retaining source data.
                if turn_id in self._pending or len(self._pending) < 16:
                    self._pending[turn_id] = _latest_usage(usage, self._pending.get(turn_id))
            elif turn_id == self._turn_id:
                self._update_usage(_latest_usage(usage, self._usage))

    def _update_usage(self, usage):
        previous = self._usage.get('total_tokens') if self._usage is not None else None
        self._usage = usage
        if self._owner._budget is not None and usage is not None:
            total = usage.get('total_tokens')
            if total is not None and total != previous:
                self._owner._budget._record_total(previous, total)


def _latest_usage(current, previous):
    if previous is None or 'total_tokens' not in previous:
        return current
    # Keep the last cumulative report intact when a later message omits its
    # total or carries an older total from the same ephemeral thread.
    if 'total_tokens' not in current or current['total_tokens'] < previous['total_tokens']:
        return previous
    return current


class GenerationMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._requests = []
        self._budget = None

    def attach_budget(self, budget):
        with self._lock:
            if self._budget is budget:
                return
            if self._budget is not None or self._requests:
                raise RuntimeError('Attach a token allowance before starting generation requests.')
            self._budget = budget

    def begin_model_request(self, thread_id):
        request = _ModelRequest(self, thread_id)
        with self._lock:
            if self._budget is not None:
                self._budget._model_started()
            self._requests.append(request)
        return request

    def live_usage(self):
        with self._lock:
            totals = [request._usage['total_tokens'] for request in self._requests
                      if request._usage is not None and 'total_tokens' in request._usage]
            return {'total_tokens': sum(totals) if totals else None, 'reported_requests': len(totals),
                    'model_requests': len(self._requests)}

    def snapshot(self):
        with self._lock:
            usages = [dict(request._usage) if request._usage is not None else None
                      for request in self._requests]
        reported = [usage for usage in usages if usage is not None]
        fields = tuple(_FIELDS.values())
        coverage = {name: sum(name in usage for usage in reported) for name in fields}
        totals = {name: sum(usage[name] for usage in reported if name in usage)
                  if coverage[name] else None for name in fields}
        complete = bool(usages) and all(coverage[name] == len(usages) for name in fields)
        return {
            'model_requests': len(usages),
            'token_usage': {
                **totals,
                'reported_requests': len(reported),
                'unreported_requests': len(usages) - len(reported),
                'field_coverage': coverage,
                'status': 'complete' if complete else 'partial' if reported else 'unavailable',
            },
        }


@contextmanager
def collect_generation_metrics(collection):
    token = _current.set(collection)
    try:
        yield collection
    finally:
        _current.reset(token)


def begin_model_request(thread_id):
    """Return a recorder when collection is enabled, otherwise remain a no-op."""
    collection = _current.get()
    if collection is None:
        from .progress import current_metrics
        collection = current_metrics()
    return collection.begin_model_request(thread_id) if collection is not None else None
