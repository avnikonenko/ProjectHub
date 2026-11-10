"""Generation counters are scoped, observable during work, and reset on retry."""
import json
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, CodexSession
from backend.app.integrations.codex import progress
from backend.app.integrations.codex.archive import archive_history


def test_progress_is_scoped_and_reads_are_snapshots():
    project_id = 91001
    progress.begin(project_id, 'brief')
    started_at = progress.read(project_id, 'brief')['started_at']
    assert started_at.endswith('+00:00')
    progress.update(project_id, stage='Collecting', sessions=2, turns=5)
    with ThreadPoolExecutor(max_workers=4) as threads:
        snapshots = list(threads.map(lambda _: progress.read(project_id, 'brief'), range(8)))
    assert all(item['state'] == 'running' and item['turns'] == 5 for item in snapshots)
    snapshots[0]['sessions'] = 100
    assert progress.read(project_id, 'brief')['sessions'] == 2
    assert progress.read(project_id, 'qa')['state'] == 'idle'
    assert progress.read(project_id + 1, 'brief')['state'] == 'idle'
    progress.finish(project_id, error='Transport failed')
    progress.update(project_id, turns=999)
    assert progress.read(project_id, 'brief') == {
        'state': 'failed', 'stage': 'Failed', 'error': 'Transport failed', 'turns': 5, 'sessions': 2, 'batches': 0,
        'started_at': started_at, 'budget_seconds': 900}
    progress.begin(project_id, 'qa')
    progress.finish(project_id)
    assert progress.read(project_id, 'qa')['state'] == 'completed'
    assert progress.read(project_id, 'brief')['state'] == 'failed'
    progress.begin(project_id, 'brief')
    assert progress.read(project_id, 'brief')['turns'] == 0
    progress.finish(project_id)


def test_archive_reports_scoped_counts_and_cached_batches(tmp_path):
    project_id = 92001
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add(Project(id=project_id, name='Confidential project')); db.flush()
        db.add(CodexSession(project_id=project_id, thread_id='private-thread')); db.commit()

    class Manager:
        generations = 0

        def _new_read_client(self):
            return self

        def history(self, session, cursor, *, client, archive):
            assert progress.read(project_id, 'brief')['stage'] == 'Reading session 1 of 1'
            return {'turns': [{'id': 'A', 'status': 'completed', 'messages': [{'role': 'user', 'text': 'Private'}]},
                              {'id': 'B', 'status': 'inProgress', 'messages': []}], 'next_cursor': None}

        def generate_brief(self, prompt, schema):
            self.generations += 1
            current = progress.read(project_id, 'brief')
            assert current['turns'] == 1 and current['batches'] == 0
            assert current['stage'] == 'Summarizing history batch'
            return json.dumps({'evidence': []})

        def close(self):
            pass

    manager = Manager()
    try:
        for stage in ('Summarizing history batch', 'Reusing cached evidence batch'):
            progress.begin(project_id, 'brief')
            try:
                archive_history(engine, project_id, manager)
                current = progress.read(project_id, 'brief')
                assert (current['sessions'], current['turns'], current['batches']) == (1, 1, 1)
                assert current['stage'] == stage
            finally:
                progress.finish(project_id)
        assert manager.generations == 1
    finally:
        engine.dispose()


def test_cancel_is_scoped_to_observed_generation_and_clears_context():
    import pytest
    from backend.app.integrations.codex.protocol import CodexError

    pid = 93001
    progress.begin(pid, 'qa')
    old = progress.read(pid, 'qa')['started_at']
    event = progress.current_cancel_event()
    try:
        for project_id, kind, started_at in ((pid + 1, 'qa', old), (pid, 'brief', old), (pid, 'qa', 'stale')):
            with pytest.raises(CodexError, match='no longer running'):
                progress.cancel(project_id, kind, started_at)
        assert not event.is_set()
        state = progress.cancel(pid, 'qa', old)
        assert state['state'] == 'running' and state['stage'] == 'Cancelling'
        assert state['cancel_requested'] is True and event.is_set()
        with pytest.raises(progress.GenerationCancelled):
            progress.check_cancelled()
        with pytest.raises(progress.GenerationCancelled):
            progress.update(pid, turns=4)
        with pytest.raises(progress.GenerationCancelled):
            progress.complete(pid, lambda: None)
    finally:
        progress.finish(pid, error='Any later error')
    assert progress.current_cancel_event() is None
    assert progress.read(pid, 'qa')['state'] == 'cancelled'
    assert progress.read(pid, 'qa')['error'] is None
    progress.begin(pid, 'qa')
    try:
        assert progress.current_cancel_event() is not event
        progress.check_cancelled()
        with pytest.raises(CodexError):
            progress.cancel(pid, 'qa', old)
    finally:
        progress.finish(pid)


def test_successful_save_and_cancellation_are_atomic():
    from threading import Event
    import pytest
    from backend.app.integrations.codex.protocol import CodexError

    pid = 93002
    entered, attempted = Event(), Event()
    progress.begin(pid, 'brief')
    started = progress.read(pid, 'brief')['started_at']

    def cancel():
        entered.wait(2)
        attempted.set()
        with pytest.raises(CodexError, match='no longer running'):
            progress.cancel(pid, 'brief', started)

    def save():
        entered.set()
        assert attempted.wait(2)
        return 'saved'

    with ThreadPoolExecutor(max_workers=1) as threads:
        future = threads.submit(cancel)
        assert progress.complete(pid, save) == 'saved'
        future.result(timeout=2)
    assert progress.read(pid, 'brief')['state'] == 'completed'
    assert progress.current_cancel_event() is None


def test_save_failure_keeps_active_generation_for_error_finalization():
    import pytest

    pid = 93003
    progress.begin(pid, 'scheme')
    try:
        def save():
            raise RuntimeError('Commit failed')
        with pytest.raises(RuntimeError, match='Commit failed'):
            progress.complete(pid, save)
        assert progress.read(pid, 'scheme')['state'] == 'running'
    finally:
        progress.finish(pid, error='Commit failed')
    assert progress.read(pid, 'scheme')['state'] == 'failed'
