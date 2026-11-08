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
        'started_at': started_at}
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
