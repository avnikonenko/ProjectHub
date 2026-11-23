from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from backend.app.integrations.codex import generation_token_budget as budgets
from backend.app.integrations.codex.generation_metrics import GenerationMetrics
from backend.app.integrations.codex.protocol import CodexError


def _usage(thread, turn, **counts):
    return {'threadId': thread, 'turnId': turn, 'tokenUsage': {'total': counts}}


def test_parallel_collectors_share_reported_deltas_and_reject_new_requests():
    budget = budgets.TokenBudget(100)
    collections = [GenerationMetrics(), GenerationMetrics()]
    for collection in collections:
        collection.attach_budget(budget)

    first = collections[0].begin_model_request('first')
    second = collections[1].begin_model_request('second')
    # A report may arrive before acknowledgement, but foreign turns contribute
    # no usage and repeated or older totals for the acknowledged turn add none.
    first.record(_usage('first', 'foreign', totalTokens=500))
    first.record(_usage('first', 'turn', totalTokens=30))
    first.bind_turn('turn')
    first.record(_usage('first', 'turn', totalTokens=30))
    first.record(_usage('first', 'turn', totalTokens=1))
    second.bind_turn('turn')
    second.record(_usage('second', 'turn', outputTokens=100))
    assert budget.status() == {
        'limit': 100, 'total_tokens': 30, 'reported_requests': 1,
        'model_requests': 2, 'reached': False, 'active_jobs': 0,
    }
    second.record(_usage('second', 'turn', totalTokens=70))
    with pytest.raises(budgets.GenerationTokenLimitExceeded, match='100 reported tokens'):
        collections[0].begin_model_request('additional')
    assert collections[0].live_usage()['model_requests'] == 1

    first.record(_usage('first', 'turn', totalTokens=45))
    assert budget.status() == {
        'limit': 100, 'total_tokens': 115, 'reported_requests': 2,
        'model_requests': 2, 'reached': True, 'active_jobs': 0,
    }


def test_simultaneous_reports_do_not_lose_shared_usage():
    budget = budgets.TokenBudget(0)
    first_report = threading.Barrier(8)

    def report_usage(index):
        metrics = GenerationMetrics()
        metrics.attach_budget(budget)
        request = metrics.begin_model_request(str(index))
        request.bind_turn('turn')
        first_report.wait(3)
        request.record(_usage(str(index), 'turn', totalTokens=10))
        request.record(_usage(str(index), 'turn', totalTokens=25))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(report_usage, range(8)))
    budget.check()
    assert budget.status() == {
        'limit': 0, 'total_tokens': 200, 'reported_requests': 8,
        'model_requests': 8, 'reached': False, 'active_jobs': 0,
    }


def test_group_original_limit_project_and_engine_isolation(monkeypatch):
    class Engine:
        pass

    first_engine = Engine()
    other_engine = Engine()
    monkeypatch.setattr(budgets, '_actions', {})
    with budgets.use_action_budget(1, 'action', 100, engine=first_engine) as group:
        assert group.limit == 100
    with budgets.use_action_budget(1, 'action', 200, engine=first_engine) as late:
        assert late is group
    assert budgets.action_budget_status(2, 'action', engine=first_engine) is None
    assert budgets.action_budget_status(1, 'action', engine=other_engine) is None
    with budgets.use_action_budget(1, 'action', 200, engine=other_engine) as other_group:
        assert other_group.limit == 200
    assert budgets.action_budget_status(1, 'action', engine=first_engine)['limit'] == 100


def test_active_group_is_protected_from_expiry_and_capacity_eviction(monkeypatch):
    now = [10]
    monkeypatch.setattr(budgets, 'monotonic', lambda: now[0])
    monkeypatch.setattr(budgets, '_actions', {})
    monkeypatch.setattr(budgets, '_MAX_ACTIONS', 1)
    with budgets.use_action_budget(1, 'action', 100) as budget:
        assert budget.status()['total_tokens'] is None
        assert budgets.current_token_budget() is budget
        now[0] += budgets._ACTION_RETENTION_SECONDS
        assert budgets.action_budget_status(1, 'action')['active_jobs'] == 1
        with pytest.raises(CodexError, match='too many recent generation actions'):
            with budgets.use_action_budget(1, 'other-action', 100):
                pytest.fail('A full active registry admitted another action')
    assert budgets.current_token_budget() is None
    assert budgets.action_budget_status(1, 'action')['active_jobs'] == 0
    now[0] += budgets._ACTION_RETENTION_SECONDS
    assert budgets.action_budget_status(1, 'action') is None
    with budgets.use_action_budget(1, 'other-action', 100) as new_budget:
        assert new_budget is not budget


def test_rejected_sibling_preserves_the_active_and_completed_action(monkeypatch):
    class Busy(Exception):
        pass

    monkeypatch.setattr(budgets, '_actions', {})
    sibling_started, finish_sibling = threading.Event(), threading.Event()

    def succeed():
        with budgets.use_action_budget(1, 'action', 50) as sibling:
            sibling_started.set()
            assert finish_sibling.wait(5)
            return sibling

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(succeed)
        try:
            assert sibling_started.wait(5)
            with pytest.raises(Busy):
                with budgets.use_action_budget(1, 'action', 100, discard_on=(Busy,)) as rejected:
                    assert rejected.limit == 50
                    raise Busy()
            assert budgets.action_budget_status(1, 'action')['active_jobs'] == 1
        finally:
            finish_sibling.set()
        assert pending.result(timeout=5) is rejected

    with pytest.raises(Busy):
        with budgets.use_action_budget(1, 'action', 100, discard_on=(Busy,)) as late:
            assert late is rejected
            raise Busy()
    assert budgets.action_budget_status(1, 'action')['model_requests'] == 0


def test_last_rejected_holder_releases_an_unused_action(monkeypatch):
    class Busy(Exception):
        pass

    monkeypatch.setattr(budgets, '_actions', {})
    monkeypatch.setattr(budgets, '_MAX_ACTIONS', 1)
    with pytest.raises(Busy):
        with budgets.use_action_budget(1, 'action', 50, discard_on=(Busy,)) as first:
            with pytest.raises(Busy):
                with budgets.use_action_budget(1, 'action', 100, discard_on=(Busy,)) as second:
                    assert first is second
                    raise Busy()
            assert budgets.action_budget_status(1, 'action')['active_jobs'] == 1
            raise Busy()
    assert budgets.action_budget_status(1, 'action') is None
    with budgets.use_action_budget(1, 'new', 20):
        assert budgets.action_budget_status(1, 'new')['active_jobs'] == 1
