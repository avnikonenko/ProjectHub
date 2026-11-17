"""Durable outcomes use the artifact transaction, not per-batch writes or AI."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import generation_attempts as attempts, progress
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import GenerationRevision, Project


@pytest.fixture
def engine(tmp_path):
    value, _ = initialize(tmp_path / 'data')
    with Session(value) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')]); db.commit()
    yield value
    value.dispose()


def latest(engine, kind='brief'):
    with Session(engine) as db:
        return attempts.read_latest(db, 1, kind)


def test_completed_attempt_and_artifact_commit_together_and_read_after_memory_loss(engine, monkeypatch):
    progress.begin(1, 'brief', engine=engine)
    started = progress.read(1, 'brief')['started_at']
    assert latest(engine)['state'] == 'running'
    with Session(engine) as db:
        db.add(GenerationRevision(project_id=1, kind='brief', generated_at='new', snapshot={'saved': True}))
        progress.complete(1, db.commit, db=db)
    assert latest(engine)['state'] == 'completed'
    with Session(engine) as db:
        assert db.query(GenerationRevision).count() == 1
    progress.finish(1)  # legacy second finish cannot finalize another job
    monkeypatch.delitem(progress._snapshots, (1, 'brief'))
    saved = progress.read(1, 'brief', engine=engine)
    assert saved['state'] == 'completed' and saved['started_at'] == started
    assert saved['latest_attempt']['state'] == 'completed'


def test_failed_commit_rolls_back_attempt_and_artifact_then_records_failure(engine):
    progress.begin(1, 'brief', engine=engine)
    try:
        with Session(engine) as db:
            db.add(GenerationRevision(project_id=1, kind='brief', generated_at='new', snapshot={'saved': True}))
            def failed():
                db.flush()
                raise RuntimeError('Synthetic commit failure')
            with pytest.raises(RuntimeError):
                progress.complete(1, failed, db=db)
            db.rollback()
        assert latest(engine)['state'] == 'running'
        progress.finish(1, error='Synthetic commit failure')
        assert latest(engine)['state'] == 'failed'
        with Session(engine) as db:
            assert db.query(GenerationRevision).count() == 0
    finally:
        progress.finish(1)


def test_cancel_and_timeout_persist_without_affecting_saved_artifacts(engine):
    progress.begin(1, 'brief', engine=engine)
    progress.cancel(1, 'brief', progress.read(1, 'brief')['started_at'])
    with pytest.raises(progress.GenerationCancelled):
        with Session(engine) as db:
            progress.complete(1, db.commit, db=db)
    progress.finish(1, error='Other transport error')
    assert latest(engine)['state'] == 'cancelled'
    progress.begin(1, 'brief', engine=engine)
    progress.finish(1, error='AI generation exceeded its overall time budget.')
    assert latest(engine)['state'] == 'failed' and latest(engine)['error_category'] == 'timeout'


def test_startup_interruption_and_database_identity_do_not_expose_another_database(engine, tmp_path):
    progress.begin(1, 'brief', engine=engine)
    progress.finish(1, error='Private database failure')
    other, _ = initialize(tmp_path / 'other')
    try:
        with Session(other) as db:
            db.add(Project(id=1, name='Different One')); db.commit()
        assert progress.read(1, 'brief', engine=other)['state'] == 'idle'
        attempts.start(other, 1, 'brief', '2026-11-17T12:00:00+00:00')
        attempts.interrupt_running(other)
        saved = progress.read(1, 'brief', engine=other)
        assert saved['state'] == 'failed' and saved['latest_attempt']['state'] == 'interrupted'
        assert 'Private database failure' not in str(saved)
    finally:
        other.dispose()


def test_stale_worker_cannot_persist_failure_or_completion_for_retry(engine):
    progress.begin(1, 'brief', engine=engine)
    stale = copy_context()
    progress.finish(1, error='First attempt failed')
    progress.begin(1, 'brief', engine=engine)
    current = latest(engine)['started_at']
    stale.run(progress.finish, 1, error='Stale transport error')
    assert latest(engine)['started_at'] == current and latest(engine)['state'] == 'running'
    with pytest.raises(CodexError, match='no longer running'):
        stale.run(progress.complete, 1, lambda: None)
    assert latest(engine)['state'] == 'running'
    progress.finish(1)


def test_preview_marks_unsaved_scope_and_new_dependency_replaces_old_memory_outcome(engine):
    progress.begin(1, 'knowledge', engine=engine, saves_result=False)
    with Session(engine) as db:
        progress.complete(1, db.commit, db=db)
    assert latest(engine, 'knowledge')['saves_result'] is False
    progress.begin(1, 'scheme', engine=engine)
    progress.finish(1)
    token = attempts.queue_dependency(engine, 1)
    assert token is not None
    saved = progress.read(1, 'scheme', engine=engine)
    assert saved['stage'] == 'Waiting for Knowledge' and saved['latest_attempt']['state'] == 'waiting'


def test_multiple_views_persist_independent_outcomes(engine):
    def run(kind):
        progress.begin(1, kind, engine=engine)
        progress.finish(1, error='Failed' if kind == 'brief' else None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, ('brief', 'scheme')))
    assert latest(engine, 'brief')['state'] == 'failed'
    assert latest(engine, 'scheme')['state'] == 'completed'


def test_application_startup_marks_old_running_and_waiting_attempts_interrupted(tmp_path):
    directory = tmp_path / 'restart-data'
    engine, _ = initialize(directory)
    with Session(engine) as db:
        db.add(Project(id=1, name='Restart fixture')); db.commit()
    attempts.start(engine, 1, 'knowledge', '2026-11-17T12:00:00+00:00')
    attempts.queue_dependency(engine, 1)
    engine.dispose()
    from backend.app.main import create_app
    app = create_app(directory)
    try:
        with Session(app.state.engine) as db:
            assert attempts.read_latest(db, 1, 'knowledge')['state'] == 'interrupted'
            assert attempts.read_latest(db, 1, 'scheme')['state'] == 'interrupted'
    finally:
        app.state.engine.dispose()


@pytest.mark.parametrize('cancelled', [False, True])
def test_tags_persist_terminal_progress_with_the_metadata_transaction(engine, monkeypatch, cancelled):
    from backend.app.integrations.codex import tag_generation as tags
    from backend.app.models import ProjectKnowledge
    original = {'project_id': 1, 'publication_id': 'saved', 'items': [], 'topic_groups': []}
    with Session(engine) as db:
        db.add(ProjectKnowledge(project_id=1, snapshot=original)); db.commit()

    def group(*args, **kwargs):
        if cancelled:
            status = progress.read(1, 'tags')
            progress.cancel(1, 'tags', status['started_at'])
        return {'topic_groups': [], 'topic_grouping': {'updated': True}}

    monkeypatch.setattr(tags, 'group_records', group)
    if cancelled:
        with pytest.raises(progress.GenerationCancelled):
            tags.generate_tags(engine, 1, object(), target='knowledge', publication_id='saved')
    else:
        result = tags.generate_tags(engine, 1, object(), target='knowledge', publication_id='saved')
        assert result['publication_id'] == 'saved'
    state = progress.read(1, 'tags', engine=engine)
    assert state['state'] == ('cancelled' if cancelled else 'completed')
    assert state['latest_attempt']['state'] == state['state']
    with Session(engine) as db:
        saved = db.get(ProjectKnowledge, 1).snapshot
    assert saved == (original if cancelled else {**original, 'topic_grouping': {'updated': True}})


def test_recreated_project_id_does_not_inherit_terminal_memory_after_attempt_cascade(engine):
    progress.begin(1, 'brief', engine=engine)
    progress.finish(1, error='The deleted project failed')
    assert progress.read(1, 'brief', engine=engine)['state'] == 'failed'
    with Session(engine) as db:
        db.delete(db.get(Project, 1)); db.flush()
        db.add(Project(id=1, name='New owner of reused ID')); db.commit()
    status = progress.read(1, 'brief', engine=engine)
    assert status['state'] == 'idle'
    assert 'The deleted project failed' not in str(status)
    assert 'latest_attempt' not in status


def test_new_known_failure_is_not_replaced_by_an_older_durable_attempt(engine, monkeypatch):
    progress.begin(1, 'brief', engine=engine)
    progress.finish(1)
    previous = latest(engine)['started_at']
    def unavailable(*args, **kwargs):
        raise RuntimeError('Synthetic attempt storage failure')
    monkeypatch.setattr(attempts, 'start', unavailable)
    with pytest.raises(CodexError, match='Could not save generation attempt status'):
        progress.begin(1, 'brief', engine=engine)
    status = progress.read(1, 'brief', engine=engine)
    assert status['state'] == 'failed'
    assert status['started_at'] > previous
    assert status['latest_attempt']['started_at'] == previous
