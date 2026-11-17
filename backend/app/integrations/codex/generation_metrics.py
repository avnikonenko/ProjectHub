"""Best-effort, request-local usage from official App Server notifications.

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
            self._usage = self._pending.get(turn_id)
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
                    previous = self._pending.get(turn_id)
                    if not _older(usage, previous):
                        self._pending[turn_id] = usage
            elif turn_id == self._turn_id and not _older(usage, self._usage):
                self._usage = usage


def _older(current, previous):
    # A fresh ephemeral thread is used for each request. Its cumulative total
    # cannot decrease; ignore a delayed earlier sample rather than double-count.
    return (previous is not None and 'total_tokens' in current and 'total_tokens' in previous
            and current['total_tokens'] < previous['total_tokens'])


class GenerationMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._requests = []

    def begin_model_request(self, thread_id):
        request = _ModelRequest(self, thread_id)
        with self._lock:
            self._requests.append(request)
        return request

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
    return collection.begin_model_request(thread_id) if collection is not None else None
