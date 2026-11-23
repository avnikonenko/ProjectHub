from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace

import pytest

from backend.app.integrations.codex import archive, knowledge, progress


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


@pytest.mark.parametrize('initial,changed,expected,use_auto', [
    ('10', '0', 10, False),
    ('0', '10', 2, True),
])
def test_mode_and_batch_count_stay_together_when_settings_change(monkeypatch, initial, changed, expected, use_auto):
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', initial)
    monkeypatch.setattr(progress, 'resolve_extraction_workers', lambda value: value or 2)
    manager = SimpleNamespace(independent_generation=lambda: None)
    progress.begin(1, 'brief')
    try:
        monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', changed)
        with archive.archive_worker_scope() as archive_workers, progress.generation_request_slot():
            assert archive_workers == knowledge._extraction_workers(manager) == expected
            assert progress.automatic_worker_status()['active'] == int(use_auto)
    finally:
        progress.finish(1)
    with archive.archive_worker_scope() as archive_workers:
        assert archive_workers == (10 if use_auto else 2)


def test_job_uses_the_same_settings_snapshot_as_its_profile(monkeypatch):
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '10')
    settings = {'selected_extraction_workers': 0, 'extraction_workers': 3}
    progress.begin(1, 'knowledge', worker_settings=settings)
    try:
        with archive.archive_worker_scope() as archive_workers, progress.generation_request_slot():
            assert archive_workers == 3
            assert progress.automatic_worker_status()['active'] == 1
    finally:
        progress.finish(1)
