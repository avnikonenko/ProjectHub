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
    terminal = progress.read(project_id, 'brief')
    assert terminal.pop('elapsed_seconds') >= 0
    assert terminal.pop('stage_seconds')
    assert terminal == {
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


def test_same_project_views_have_independent_progress_cancel_and_save():
    from contextvars import copy_context
    import pytest

    pid = 94001
    # Separate request contexts, as FastAPI and worker copy_context provide.
    overview, knowledge = copy_context(), copy_context()
    overview.run(progress.begin, pid, 'brief')
    knowledge.run(progress.begin, pid, 'knowledge')
    try:
        overview.run(progress.update, pid, stage='Overview read', batch_completed=3)
        knowledge.run(progress.update, pid, stage='Knowledge read', batch_completed=7)
        assert progress.read(pid, 'brief')['batch_completed'] == 3
        assert progress.read(pid, 'knowledge')['batch_completed'] == 7
        overview.run(progress.record_diagnostics, pid, {'owner': 'overview'})
        knowledge.run(progress.record_diagnostics, pid, {'owner': 'knowledge'})
        assert progress.read(pid, 'brief')['diagnostics']['owner'] == 'overview'
        assert progress.read(pid, 'knowledge')['diagnostics']['owner'] == 'knowledge'
        progress.cancel(pid, 'brief', progress.read(pid, 'brief')['started_at'])
        with pytest.raises(progress.GenerationCancelled):
            overview.run(progress.check_cancelled)
        knowledge.run(progress.check_cancelled)
        saved = []
        assert knowledge.run(progress.complete, pid, lambda: saved.append('knowledge')) is None
        assert saved == ['knowledge']
        assert progress.read(pid, 'knowledge')['state'] == 'completed'
        # Existing caller patterns finish once more after complete; this must
        # never finalize the sibling view which is still active/cancelling.
        knowledge.run(progress.finish, pid)
        assert progress.read(pid, 'brief')['state'] == 'running'
    finally:
        overview.run(progress.finish, pid)
        knowledge.run(progress.finish, pid)
    assert progress.read(pid, 'brief')['state'] == 'cancelled'


def test_budget_is_per_job_and_stale_context_cannot_touch_a_retry():
    from contextvars import copy_context
    import time
    import pytest
    from backend.app.integrations.codex.protocol import CodexError

    pid = 94002
    first, other = copy_context(), copy_context()
    first.run(progress.begin, pid, 'brief')
    other.run(progress.begin, pid, 'scheme')
    stale = first.copy()
    try:
        progress._clocks[pid, 'brief'] = (time.monotonic() - 31, 30)
        with pytest.raises(CodexError, match='overall time budget'):
            first.run(progress.check_cancelled)
        other.run(progress.check_cancelled)
        first.run(progress.finish, pid, error='expired')
        first.run(progress.begin, pid, 'brief')
        first.run(progress.update, pid, turns=4)
        stale.run(progress.update, pid, turns=999)
        with pytest.raises(CodexError, match='no longer running'):
            stale.run(progress.check_cancelled)
        stale.run(progress.finish, pid, error='late worker')
        assert progress.read(pid, 'brief')['turns'] == 4
        assert progress.read(pid, 'brief')['state'] == 'running'
        with pytest.raises(CodexError, match='no longer running'):
            stale.run(progress.complete, pid, lambda: pytest.fail('Stale run saved'))
        other.run(progress.complete, pid, lambda: None)
        assert progress.read(pid, 'scheme')['state'] == 'completed'
        assert progress.read(pid, 'brief')['state'] == 'running'
    finally:
        first.run(progress.finish, pid)
        other.run(progress.finish, pid)


def test_duplicate_progress_begin_does_not_replace_active_run():
    from contextvars import copy_context
    import pytest

    pid = 94003
    first, duplicate = copy_context(), copy_context()
    first.run(progress.begin, pid, 'qa')
    try:
        before = progress.read(pid, 'qa')
        with pytest.raises(RuntimeError, match='already active'):
            duplicate.run(progress.begin, pid, 'qa')
        duplicate.run(progress.finish, pid, error='duplicate rejected')
        assert progress.read(pid, 'qa')['started_at'] == before['started_at']
        assert progress.read(pid, 'qa')['state'] == 'running'
    finally:
        first.run(progress.finish, pid)


def test_parallel_database_saves_do_not_hold_global_progress_lock(tmp_path):
    """A writer acquired before complete must not deadlock another committing view."""
    import sqlite3
    from threading import Event

    pid = 94004
    path = tmp_path / 'publication.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE publications (kind TEXT)')
    writer_ready, second_save_entered = Event(), Event()

    def overview():
        progress.begin(pid, 'brief')
        try:
            with sqlite3.connect(path, timeout=2) as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("INSERT INTO publications VALUES ('brief')")
                writer_ready.set()
                assert second_save_entered.wait(2)
                progress.complete(pid, db.commit)
        finally:
            progress.finish(pid)

    def knowledge():
        progress.begin(pid, 'knowledge')
        try:
            assert writer_ready.wait(2)
            with sqlite3.connect(path, timeout=2) as db:
                def save():
                    second_save_entered.set()
                    db.execute("INSERT INTO publications VALUES ('knowledge')")
                    db.commit()
                progress.complete(pid, save)
        finally:
            progress.finish(pid)

    with ThreadPoolExecutor(max_workers=2) as threads:
        first, second = threads.submit(overview), threads.submit(knowledge)
        first.result(timeout=4)
        second.result(timeout=4)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT kind FROM publications ORDER BY kind').fetchall() == [('brief',), ('knowledge',)]
    assert progress.read(pid, 'brief')['state'] == 'completed'
    assert progress.read(pid, 'knowledge')['state'] == 'completed'


def test_one_view_can_be_cancelled_while_another_view_is_saving():
    from threading import Event
    from contextvars import copy_context

    pid = 94005
    saving, release_save = Event(), Event()
    other = copy_context()
    other.run(progress.begin, pid, 'scheme')

    def overview():
        progress.begin(pid, 'brief')
        try:
            def save():
                saving.set()
                assert release_save.wait(2)
            progress.complete(pid, save)
        finally:
            progress.finish(pid)

    try:
        with ThreadPoolExecutor(max_workers=1) as threads:
            future = threads.submit(overview)
            try:
                assert saving.wait(2)
                started = progress.read(pid, 'scheme')['started_at']
                progress.cancel(pid, 'scheme', started)
                assert progress.read(pid, 'scheme')['cancel_requested']
                assert progress.read(pid, 'brief')['state'] == 'running'
            finally:
                release_save.set()
            future.result(timeout=2)
    finally:
        other.run(progress.finish, pid)
    assert progress.read(pid, 'scheme')['state'] == 'cancelled'
    assert progress.read(pid, 'brief')['state'] == 'completed'
