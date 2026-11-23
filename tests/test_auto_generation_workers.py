from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from backend.app.integrations.codex import progress


def test_auto_slots_are_shared_and_cancelled_waiters_do_not_take_capacity(monkeypatch):
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '0')
    monkeypatch.setattr(progress, 'resolve_extraction_workers', lambda value: 1)
    entered = threading.Event()
    release = threading.Event()
    waiting = threading.Event()

    def first():
        progress.begin(1, 'brief')
        try:
            with progress.generation_request_slot():
                entered.set()
                assert release.wait(5)
        finally:
            progress.finish(1)

    def second():
        progress.begin(2, 'knowledge')
        try:
            waiting.set()
            with progress.generation_request_slot():
                pytest.fail('A second Auto job entered while its only slot was occupied')
        finally:
            progress.finish(2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(first)
        try:
            assert entered.wait(5)
            queued = pool.submit(second)
            assert waiting.wait(5)
            progress.cancel(2, 'knowledge', progress.read(2, 'knowledge')['started_at'])
            with pytest.raises(progress.GenerationCancelled):
                queued.result(timeout=5)
        finally:
            release.set()
        active.result(timeout=5)

    with progress.generation_request_slot():
        assert progress.automatic_worker_status()['active'] == 1
    assert progress.automatic_worker_status()['active'] == 0


def test_running_auto_job_keeps_its_mode_and_fixed_jobs_bypass_auto_limit(monkeypatch):
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '0')
    monkeypatch.setattr(progress, 'resolve_extraction_workers', lambda value: 1)
    progress.begin(1, 'brief')
    try:
        monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '10')
        with progress.generation_request_slot():
            assert progress.automatic_worker_status()['active'] == 1
            with ThreadPoolExecutor(max_workers=1) as pool:
                def fixed():
                    progress.begin(2, 'qa')
                    try:
                        with progress.generation_request_slot():
                            assert progress.automatic_worker_status()['active'] == 1
                    finally:
                        progress.finish(2)

                pool.submit(fixed).result(timeout=5)
    finally:
        progress.finish(1)
