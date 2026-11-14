"""Bounded legacy generation scheduling with actual caches and mocked AI calls."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import threading
import time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.tests.integrations.test_archive import env, FakeCodex, turn
from backend.app.integrations.codex import archive, progress
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import ProjectHistoryBatch


_marker = ContextVar('archive_parallel_test_context', default=None)


class ParallelFake(FakeCodex):
    def __init__(self, *, empty=False):
        super().__init__()
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.empty = empty
        self.contexts = []
        self.phases = []

    @contextmanager
    def independent_generation(self):
        yield self

    def generate_brief(self, prompt, schema):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.contexts.append((_marker.get(), progress.current_cancel_event()))
            self.phases.append(archive._archive_run.get()['phases'])
        try:
            data = json.loads(prompt.split('\n', 1)[1])
            # Later jobs can finish before the first without changing output order.
            first = data.get('sources', [{}])[0].get('turn_id', '')
            time.sleep(.04 if first == '0' else .01)
            progress.check_cancelled()
            if self.empty:
                self.inputs.append(data)
                return '{"evidence":[]}'
            return super().generate_brief(prompt, schema)
        finally:
            with self.lock:
                self.active -= 1


def test_history_parallel_bound_order_context_and_exact_cache_replay(env):
    engine, _ = env
    manager = ParallelFake()
    manager.turns['a'] = [turn(index, 'x' * 21000) for index in range(8)]
    token = _marker.set('request-specific instructions')
    progress.begin(1, 'brief')
    try:
        event = progress.current_cancel_event()
        with archive.archive_worker_scope(3):
            result, _ = archive.archive_history(engine, 1, manager)
            diagnostics = archive.archive_diagnostics()
        assert manager.peak == 3
        assert all(marker == 'request-specific instructions' and inherited is event
                   for marker, inherited in manager.contexts)
        ids = [source['id'] for item in result for source in item['sources']]
        assert ids == [f'session:1:turn:{index}:part:{part}' for index in range(8) for part in range(1, 7)]
        count = len(manager.inputs)
        assert diagnostics['archive_generated_batches'] == count
        assert len(diagnostics['archive_batch_timings']) == count
        status = progress.read(1, 'brief')
        assert status['batch_total'] == status['batch_completed'] == count
        with archive.archive_worker_scope(1):
            replay, _ = archive.archive_history(engine, 1, manager)
            assert archive.archive_diagnostics()['archive_cache_hits'] == count
        assert replay == result and len(manager.inputs) == count
    finally:
        progress.finish(1)
        _marker.reset(token)


def points(count):
    return [{'text': f'Independent fact {i}', 'sources': [
        {'id': f'note:{i}', 'label': f'Note {i}', 'text': f'Exact original {i}'}]}
        for i in range(count)]


@pytest.mark.parametrize('count', [25, 32])
def test_parallel_reduction_never_summarizes_tail_when_first_empty_result_fits(env, count):
    engine, _ = env
    manager = ParallelFake(empty=True)
    original = points(count)
    with archive.archive_worker_scope(4):
        result = archive.summarize_evidence(engine, 1, manager, original)
        assert archive.archive_diagnostics()['archive_generated_batches'] == 1
    assert len(manager.inputs) == 1 and manager.peak == 1
    assert result == original[16:]
    assert all(actual is expected for actual, expected in zip(result, original[16:]))


def test_independent_reduction_groups_overlap_and_rounds_match_serial_cache_replay(env):
    engine, _ = env
    manager = ParallelFake()
    original = points(112)
    with archive.archive_worker_scope(3):
        result = archive.summarize_evidence(engine, 1, manager, original)
        parallel = archive.archive_diagnostics()
    assert manager.peak == 3 and len(result) <= 24
    assert manager.phases == sorted(manager.phases) and max(manager.phases) > 1
    calls = len(manager.inputs)
    assert calls == parallel['archive_generated_batches']
    with archive.archive_worker_scope(1):
        replay = archive.summarize_evidence(engine, 1, manager, original)
        serial = archive.archive_diagnostics()
    assert replay == result and len(manager.inputs) == calls
    # Every parallel reduction was also needed by the serial early-fit schedule.
    assert serial['archive_cache_hits'] == calls
    assert all(ref['id'] in {f'note:{i}' for i in range(112)} for item in result for ref in item['sources'])


def job(index):
    source = {'id': f'source:{index}', 'label': str(index), 'text': f'Original {index}'}
    return {'sources': [source]}, [source], 8


def test_failure_stops_dispatch_preserves_success_cache_and_honest_completion(env, monkeypatch):
    engine, _ = env
    manager = ParallelFake()
    committed, failed = threading.Event(), threading.Event()
    original = archive._summarize
    called = []

    def summarize(engine, project_id, selected, payload, sources, stage, limit):
        index = int(sources[0]['id'].split(':')[-1])
        called.append(index)
        if index == 0:
            assert committed.wait(2)
            failed.set()
            raise CodexError('Originating batch failure')
        result = original(engine, project_id, selected, payload, sources, stage, limit)
        committed.set()
        assert failed.wait(2)
        return result

    monkeypatch.setattr(archive, '_summarize', summarize)
    progress.begin(1, 'brief')
    try:
        with archive.archive_worker_scope(2):
            with pytest.raises(CodexError, match='Originating batch failure'):
                archive._summary_jobs(engine, 1, manager, 4, job, 'history')
            assert archive.archive_diagnostics()['archive_generated_batches'] == 1
        assert set(called) == {0, 1}
        assert progress.read(1, 'brief')['batch_completed'] == 1
        with Session(engine) as db:
            assert len(list(db.scalars(select(ProjectHistoryBatch)))) == 1
    finally:
        progress.finish(1)


def test_cancellation_context_stops_parallel_calls_and_remaining_dispatch(env, monkeypatch):
    engine, _ = env
    manager = ParallelFake()
    invoked = []

    def cancel(engine, project_id, selected, payload, sources, stage, limit):
        invoked.append(sources[0]['id'])
        status = progress.read(1, 'brief')
        progress.cancel(1, 'brief', status['started_at'])
        progress.check_cancelled()

    monkeypatch.setattr(archive, '_summarize', cancel)
    progress.begin(1, 'brief')
    try:
        with archive.archive_worker_scope(2):
            with pytest.raises(progress.GenerationCancelled):
                archive._summary_jobs(engine, 1, manager, 10, job, 'history')
        assert len(invoked) <= 2
    finally:
        progress.finish(1)
    assert progress.read(1, 'brief')['state'] == 'cancelled'


def test_duplicate_parallel_cache_misses_return_winner_and_replay_identically(env):
    engine, _ = env
    barrier = threading.Barrier(2)

    class RaceManager(ParallelFake):
        def generate_brief(self, prompt, schema):
            data = json.loads(prompt.split('\n', 1)[1])
            with self.lock:
                self.inputs.append(data)
                count = len(self.inputs)
            barrier.wait(timeout=2)
            return json.dumps({'evidence': [{'text': f'Interpretation {count}',
                                            'source_ids': ['source:0']}]})

    manager = RaceManager()
    with archive.archive_worker_scope(2):
        result, hits, generated = archive._summary_jobs(engine, 1, manager, 2, lambda _: job(0), 'reduction')
    assert generated == 2 and hits == 0 and len(manager.inputs) == 2
    assert result[0][0] == result[1][0]
    with archive.archive_worker_scope(2):
        replay, hits, generated = archive._summary_jobs(engine, 1, manager, 2, lambda _: job(0), 'reduction')
    assert generated == 0 and hits == 2
    assert [value[0] for value in replay] == [value[0] for value in result]
    assert len(manager.inputs) == 2


def test_saved_worker_setting_is_captured_for_the_job_not_changed_mid_generation(monkeypatch):
    selected = {'extraction_workers': 2}
    monkeypatch.setattr(archive, 'ai_worker_settings', lambda: dict(selected))
    manager = ParallelFake()
    with archive.archive_worker_scope():
        assert archive._worker_count(manager) == 2
        selected['extraction_workers'] = 4
        with archive.archive_worker_scope():
            assert archive._worker_count(manager) == 2
    with archive.archive_worker_scope():
        assert archive._worker_count(manager) == 4


def test_parallel_workers_share_the_overall_budget(env, monkeypatch):
    engine, _ = env
    manager = ParallelFake()
    invoked = []

    def expire(engine, project_id, selected, payload, sources, stage, limit):
        invoked.append(sources[0]['id'])
        with progress._lock:
            progress._clocks[project_id] = (time.monotonic() - 60, 30)
        progress.check_cancelled()

    monkeypatch.setattr(archive, '_summarize', expire)
    progress.begin(1, 'brief')
    try:
        with archive.archive_worker_scope(2):
            with pytest.raises(CodexError, match='overall time budget'):
                archive._summary_jobs(engine, 1, manager, 10, job, 'history')
        assert len(invoked) <= 2
    finally:
        progress.finish(1)
