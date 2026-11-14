"""Usage accounting contracts with official-shaped events, not billed cost."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
import json
import threading

import pytest

from backend.app.integrations.codex.generation_metrics import (
    GenerationMetrics, begin_model_request, collect_generation_metrics,
)
from backend.app.integrations.codex.protocol import CodexError
from backend.tests.integrations.test_manager import env, ReusableBriefClient


def notification(thread='private-thread', turn='private-turn', total=100, **changes):
    usage = {'totalTokens': total, 'inputTokens': total - 20, 'outputTokens': 20,
             'cachedInputTokens': 10, 'reasoningOutputTokens': 5}
    usage.update(changes)
    return {'threadId': thread, 'turnId': turn, 'tokenUsage': {'total': usage,
            # "last" is not cumulative and must never replace the total.
            'last': {'totalTokens': 1, 'inputTokens': 1, 'outputTokens': 0}}}


def test_absent_usage_is_unavailable_not_invented_zero():
    metrics = GenerationMetrics()
    metrics.begin_model_request('private-thread').bind_turn('private-turn')
    actual = metrics.snapshot()
    assert actual['model_requests'] == 1
    assert actual['token_usage']['status'] == 'unavailable'
    assert actual['token_usage']['total_tokens'] is None
    assert actual['token_usage']['input_tokens'] is None
    assert actual['token_usage']['reported_requests'] == 0
    assert actual['token_usage']['unreported_requests'] == 1


def test_zero_requests_does_not_fabricate_reported_tokens():
    metrics = GenerationMetrics()
    assert metrics.snapshot()['model_requests'] == 0
    assert metrics.snapshot()['token_usage']['status'] == 'unavailable'
    assert metrics.snapshot()['token_usage']['total_tokens'] is None
    assert begin_model_request('not-collected') is None


def test_explicitly_reported_zero_is_distinct_from_missing_usage():
    metrics = GenerationMetrics()
    request = metrics.begin_model_request('private-thread')
    request.bind_turn('private-turn')
    request.record(notification(total=0, inputTokens=0, outputTokens=0,
                                cachedInputTokens=0, reasoningOutputTokens=0))
    usage = metrics.snapshot()['token_usage']
    assert usage['status'] == 'complete'
    assert usage['reported_requests'] == 1
    assert usage['total_tokens'] == 0
    assert usage['input_tokens'] == 0


def test_pre_ack_usage_binds_only_matching_turn_and_deduplicates_updates():
    metrics = GenerationMetrics()
    request = metrics.begin_model_request('private-thread')
    request.record(notification(turn='foreign-turn', total=9000))
    request.record(notification(total=80))
    request.record(notification(total=100))
    request.bind_turn('private-turn')
    request.record(notification(thread='foreign-thread', total=8000))
    request.record(notification(turn='foreign-turn', total=7000))
    request.record(notification(total=100))
    request.record(notification(total=90))
    actual = metrics.snapshot()
    assert actual['token_usage']['total_tokens'] == 100
    assert actual['token_usage']['input_tokens'] == 80
    assert actual['token_usage']['output_tokens'] == 20
    assert actual['token_usage']['cached_input_tokens'] == 10
    assert actual['token_usage']['reasoning_output_tokens'] == 5
    assert actual['token_usage']['status'] == 'complete'
    assert actual['token_usage']['reported_requests'] == 1
    assert 'private-thread' not in json.dumps(actual)
    assert 'private-turn' not in json.dumps(actual)


@pytest.mark.parametrize('value', [-1, True, 1.5, '100', None, 2 ** 63])
def test_malformed_counts_are_ignored_without_erasing_valid_usage(value):
    metrics = GenerationMetrics()
    request = metrics.begin_model_request('private-thread')
    request.bind_turn('private-turn')
    request.record(notification())
    request.record(notification(total=110, inputTokens=value))
    assert metrics.snapshot()['token_usage']['total_tokens'] == 100


def test_partial_usage_retains_field_coverage_and_unreported_request_count():
    metrics = GenerationMetrics()
    first = metrics.begin_model_request('one')
    first.bind_turn('turn')
    first.record(notification(thread='one', turn='turn'))
    second = metrics.begin_model_request('two')
    second.bind_turn('turn')
    second.record({'threadId': 'two', 'turnId': 'turn',
                   'tokenUsage': {'total': {'inputTokens': 7}}})
    metrics.begin_model_request('unreported').bind_turn('turn')
    actual = metrics.snapshot()['token_usage']
    assert actual['status'] == 'partial'
    assert actual['total_tokens'] == 100
    assert actual['input_tokens'] == 87
    assert actual['reported_requests'] == 2
    assert actual['unreported_requests'] == 1
    assert actual['field_coverage']['input_tokens'] == 2
    assert actual['field_coverage']['total_tokens'] == 1


def test_context_is_request_local_and_copied_workers_share_only_their_collector():
    first, second = GenerationMetrics(), GenerationMetrics()

    def worker(index):
        request = begin_model_request(f'thread-{index}')
        request.bind_turn('turn')
        # Reader threads have no metrics ContextVar; their captured recorder works.
        reader = threading.Thread(target=request.record,
                                  args=(notification(thread=f'thread-{index}', turn='turn'),))
        reader.start(); reader.join(timeout=2)
        assert not reader.is_alive()

    with collect_generation_metrics(first):
        contexts = [copy_context(), copy_context()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(context.run, worker, index) for index, context in enumerate(contexts)]
            for future in futures:
                future.result(timeout=3)
        with collect_generation_metrics(second):
            worker(3)
        worker(4)
    assert first.snapshot()['model_requests'] == 3
    assert first.snapshot()['token_usage']['total_tokens'] == 300
    assert second.snapshot()['model_requests'] == 1
    assert second.snapshot()['token_usage']['total_tokens'] == 100
    assert begin_model_request('after-scope') is None


class UsageClient(ReusableBriefClient):
    def request(self, method, params):
        if method == 'turn/start':
            thread, turn = params['threadId'], f'turn-{self.thread_count}'
            self.on_event('thread/tokenUsage/updated', notification(thread='foreign', turn=turn, total=8000))
            self.on_event('thread/tokenUsage/updated', notification(thread=thread, turn='wrong', total=7000))
            self.on_event('thread/tokenUsage/updated', notification(thread=thread, turn=turn, total=80))
            self.on_event('thread/tokenUsage/updated', notification(thread=thread, turn=turn))
            self.on_event('thread/tokenUsage/updated', notification(thread=thread, turn=turn))
        return super().request(method, params)


def test_real_manager_protocol_mock_counts_each_fresh_turn_once(env):
    metrics = GenerationMetrics()
    env.manager.client_factory = UsageClient
    with collect_generation_metrics(metrics), env.manager.generation_session():
        assert env.manager.generate_brief('first input', {}) == 'answer-1'
        assert env.manager.generate_brief('second input', {}) == 'answer-2'
    snapshot = metrics.snapshot()
    assert snapshot['model_requests'] == 2
    assert snapshot['token_usage']['total_tokens'] == 200
    assert snapshot['token_usage']['status'] == 'complete'
    assert snapshot['token_usage']['reported_requests'] == 2


def test_real_manager_failed_start_is_counted_without_fabricating_usage(env):
    class FailedStart(ReusableBriefClient):
        def request(self, method, params):
            if method == 'turn/start':
                raise CodexError('Turn request disconnected')
            return super().request(method, params)

    env.manager.client_factory = FailedStart
    metrics = GenerationMetrics()
    with collect_generation_metrics(metrics), pytest.raises(CodexError, match='disconnected'):
        env.manager.generate_brief('input', {})
    assert metrics.snapshot()['model_requests'] == 1
    assert metrics.snapshot()['token_usage']['status'] == 'unavailable'


def test_snapshot_is_detached_from_collector_and_caller_events():
    metrics = GenerationMetrics()
    request = metrics.begin_model_request('private-thread')
    request.bind_turn('private-turn')
    event = notification()
    original = deepcopy(event)
    request.record(event)
    snapshot = metrics.snapshot()
    snapshot['token_usage']['field_coverage']['input_tokens'] = 90
    event['tokenUsage']['total']['inputTokens'] = 700
    assert metrics.snapshot()['token_usage']['input_tokens'] == 80
    assert metrics.snapshot()['token_usage']['field_coverage']['input_tokens'] == 1
    assert original['tokenUsage']['total']['inputTokens'] == 80
