"""Top-level generations overlap while duplicate jobs and maintenance exclude."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from backend.app.integrations.codex.generation_jobs import GenerationCoordinator


def test_distinct_views_and_projects_overlap_but_duplicate_view_is_blocked():
    gate = GenerationCoordinator()
    leases = [gate.for_job(1, 'brief'), gate.for_job(1, 'knowledge'), gate.for_job(2, 'brief')]
    for lease in leases:
        assert lease.acquire(blocking=False)
    try:
        assert gate.locked()
        assert not gate.for_job(1, 'brief').acquire(blocking=False)
        assert not gate.acquire(blocking=False)
    finally:
        for lease in leases:
            lease.release()
    assert not gate.locked()
    retry = gate.for_job(1, 'brief')
    assert retry.acquire(blocking=False)
    retry.release()


def test_maintenance_excludes_all_generations_and_releases_them_on_exit():
    gate = GenerationCoordinator()
    with gate:
        assert gate.locked()
        for kind in ('brief', 'knowledge', 'scheme', 'qa', 'parameters'):
            assert not gate.for_job(1, kind).acquire(blocking=False)
        assert not gate.acquire(blocking=False)
    lease = gate.for_job(1, 'brief')
    assert lease.acquire(blocking=False)
    lease.release()
    with pytest.raises(RuntimeError):
        lease.release()
    with pytest.raises(RuntimeError):
        gate.release()


def test_waiting_duplicate_waits_for_its_own_view_not_unrelated_jobs():
    gate = GenerationCoordinator()
    first, other = gate.for_job(1, 'brief'), gate.for_job(1, 'scheme')
    first.acquire(); other.acquire()
    started = threading.Event()
    def retry():
        started.set()
        retry = gate.for_job(1, 'brief')
        assert retry.acquire(timeout=2)
        retry.release()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(retry)
            assert started.wait(2)
            assert not future.done()
            first.release()
            future.result(timeout=2)
            assert other.locked()
    finally:
        other.release()
    assert not gate.locked()


def test_timeout_does_not_claim_a_job_or_maintenance():
    gate = GenerationCoordinator()
    with gate.for_job(1, 'brief'):
        assert not gate.acquire(timeout=0)
        assert not gate.for_job(1, 'brief').acquire(timeout=0)
    assert not gate.locked()
    with pytest.raises(ValueError):
        gate.acquire(blocking=False, timeout=1)
