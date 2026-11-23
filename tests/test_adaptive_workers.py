from concurrent.futures import ThreadPoolExecutor
import queue
import threading
import time

import pytest

from backend.app.integrations.codex import adaptive_workers


class Throttled(Exception):
    pass


class Cancelled(Exception):
    pass


def _slot(pool, ceiling, check=lambda: None):
    return pool.slot(ceiling, check_cancelled=check, is_throttle=lambda exc: isinstance(exc, Throttled))


def _wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, 'Adaptive workers did not reach the expected state'
        time.sleep(0.01)


def test_saturated_pool_adds_one_worker_and_stays_within_cpu_ceiling():
    pool = adaptive_workers.AdaptiveWorkerPool(memory_pressure=lambda: False)
    entries = queue.Queue()
    release = [threading.Event() for _ in range(6)]

    def run(index):
        with _slot(pool, 3):
            entries.put(index)
            assert release[index].wait(5)

    with ThreadPoolExecutor(max_workers=len(release)) as executor:
        futures = [executor.submit(run, index) for index in range(len(release))]
        try:
            first, second = entries.get(timeout=5), entries.get(timeout=5)
            _wait_for(lambda: pool.status(3)['waiting'] == 4)
            assert pool.status(3) == {'target': 2, 'active': 2, 'waiting': 4, 'ceiling': 3}
            release[first].set()
            entries.get(timeout=5)
            assert pool.status(3)['target'] == 2
            release[second].set()
            entries.get(timeout=5)
            entries.get(timeout=5)
            assert pool.status(3) == {'target': 3, 'active': 3, 'waiting': 1, 'ceiling': 3}
        finally:
            for event in release:
                event.set()
        for future in futures:
            future.result(timeout=5)

    assert pool.status(3) == {'target': 2, 'active': 0, 'waiting': 0, 'ceiling': 3}
    assert pool.status(1)['target'] == 1


def test_throttling_reduces_target_and_cooldown_survives_idle():
    now = [10]
    pool = adaptive_workers.AdaptiveWorkerPool(clock=lambda: now[0], memory_pressure=lambda: False)
    with _slot(pool, 6):
        with pytest.raises(Throttled):
            with _slot(pool, 6):
                raise Throttled
        assert pool.status(6) == {'target': 1, 'active': 1, 'waiting': 0, 'ceiling': 6}

    assert pool.status(6)['target'] == 1
    now[0] += adaptive_workers._THROTTLE_COOLDOWN
    assert pool.status(6)['target'] == 2


def test_low_memory_limits_new_work_without_interrupting_active_requests():
    now = [10]
    low = [False]
    pool = adaptive_workers.AdaptiveWorkerPool(clock=lambda: now[0], memory_pressure=lambda: low[0])
    with _slot(pool, 6), _slot(pool, 6):
        low[0] = True
        now[0] += adaptive_workers._MEMORY_SAMPLE_INTERVAL
        assert pool.status(6) == {'target': 1, 'active': 2, 'waiting': 0, 'ceiling': 6}
    assert pool.status(6)['target'] == 1
    low[0] = False
    now[0] += adaptive_workers._MEMORY_SAMPLE_INTERVAL
    assert pool.status(6)['target'] == 2


def test_cancelled_waiter_does_not_claim_or_leak_capacity():
    pool = adaptive_workers.AdaptiveWorkerPool(memory_pressure=lambda: False)
    stopped = threading.Event()

    def check():
        if stopped.is_set():
            raise Cancelled

    def queued():
        with _slot(pool, 1, check):
            pytest.fail('Cancelled waiter unexpectedly acquired a slot')

    with ThreadPoolExecutor(max_workers=1) as executor:
        with _slot(pool, 1):
            future = executor.submit(queued)
            _wait_for(lambda: pool.status(1)['waiting'] == 1)
            stopped.set()
            with pytest.raises(Cancelled):
                future.result(timeout=5)
            assert pool.status(1) == {'target': 1, 'active': 1, 'waiting': 0, 'ceiling': 1}
    with _slot(pool, 1):
        assert pool.status(1)['active'] == 1


def test_linux_probe_uses_inherited_cgroup_headroom_and_host_available_memory(tmp_path, monkeypatch):
    proc_mem = tmp_path / 'meminfo'
    proc_groups = tmp_path / 'cgroup'
    root = tmp_path / 'groups'
    leaf = root / 'limited' / 'leaf'
    leaf.mkdir(parents=True)
    proc_groups.write_text('0::/limited/leaf\n')
    proc_mem.write_text('MemAvailable: 1000000 kB\n')
    (leaf / 'memory.max').write_text('max\n')
    (leaf.parent / 'memory.max').write_text(f'{1024**3}\n')
    (leaf.parent / 'memory.current').write_text(f'{900 * 1024**2}\n')
    monkeypatch.setattr(adaptive_workers, '_PROC_MEMORY', proc_mem)
    monkeypatch.setattr(adaptive_workers, '_PROC_CGROUP', proc_groups)
    monkeypatch.setattr(adaptive_workers, '_CGROUP_ROOT', root)

    assert adaptive_workers._linux_memory_pressure()
    (leaf.parent / 'memory.current').write_text(f'{300 * 1024**2}\n')
    assert not adaptive_workers._linux_memory_pressure()
    proc_mem.write_text('MemAvailable: 50000 kB\n')
    assert adaptive_workers._linux_memory_pressure()
