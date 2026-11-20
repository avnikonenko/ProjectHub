"""Latest outcomes survive restarts without replacing saved project artifacts."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import (GenerationRevision, Project, ProjectBrief, ProjectGenerationAttempt,
                                ProjectKnowledge, ProjectQA, ProjectScheme)
from backend.app.integrations.codex import generation_attempts as attempts

START = '2026-11-17T00:00:00+00:00'
NEXT = '2026-11-17T01:00:00+00:00'


def add_legacy_knowledge(db, pid=1, timestamp=START, change=None, payload=''):
    publication = f'legacy-{pid}'
    snapshot = {'generated_at': timestamp, 'result': {
        'published': False, 'publication_id': publication,
        'knowledge': {'project_id': pid, 'publication_id': publication,
                      'generated_at': timestamp, 'items': [], 'payload': payload},
        'brief': {'generated_at': timestamp, 'project_state': {'publication_id': publication}},
        'baseline_brief': None, 'diagnostics': {},
    }}
    if change:
        change(snapshot)
    db.add(GenerationRevision(project_id=pid, kind='knowledge_preview',
                              generated_at=timestamp, snapshot=snapshot))
    db.commit()


def knowledge_view(db, pid=1, **kwargs):
    return next(row for row in attempts.generation_summary(db, pid, **kwargs)['views']
                if row['kind'] == 'knowledge')


def test_legacy_knowledge_is_earlier_history_not_current_and_survives_restart(engine):
    with Session(engine) as db:
        add_legacy_knowledge(db)
        view = knowledge_view(db)
        assert view['state'] == 'earlier_result' and view['history_at'] == START
        assert view['saved_at'] is None and not view['showing_previous'] and view['retryable']
        assert db.get(ProjectKnowledge, 1) is None
        assert all(row['history_at'] is None for row in attempts.generation_summary(db, 1)['views']
                   if row['kind'] != 'knowledge')
        assert knowledge_view(db, 2)['state'] == 'not_generated'
    engine.dispose()
    with Session(engine) as db:
        assert knowledge_view(db)['history_at'] == START
        assert db.get(ProjectKnowledge, 1) is None


def test_current_knowledge_takes_precedence_over_legacy_history_even_without_timestamp(engine):
    with Session(engine) as db:
        add_legacy_knowledge(db)
        db.add(ProjectKnowledge(project_id=1, snapshot={'project_id': 1, 'items': []}))
        db.commit()
        view = knowledge_view(db)
        assert view['state'] == 'saved' and view['history_at'] is None
        assert not view['retryable']


@pytest.mark.parametrize('state,error,expected', [
    ('completed', None, 'earlier_result'), ('failed', 'Source changed', 'failed'),
    ('failed', 'Request timed out', 'timed_out'), ('cancelled', None, 'cancelled'),
    ('interrupted', None, 'interrupted'), ('skipped', None, 'skipped'),
])
def test_legacy_history_does_not_hide_latest_attempt_outcome(engine, state, error, expected):
    with Session(engine) as db:
        add_legacy_knowledge(db)
    attempts.start(engine, 1, 'knowledge', NEXT)
    attempts.finish(engine, 1, 'knowledge', NEXT, state=state, error=error)
    with Session(engine) as db:
        view = knowledge_view(db)
        assert view['state'] == expected and view['history_at'] == START
        assert view['saved_at'] is None
        assert knowledge_view(db, live={'knowledge': {'state': 'running'}})['state'] == 'running'


@pytest.mark.parametrize('change', [
    lambda s: s['result'].update(published=True),
    lambda s: s['result'].update(published=0),
    lambda s: s['result']['knowledge'].update(project_id=2),
    lambda s: s['result']['knowledge'].update(project_id=True),
    lambda s: s['result']['knowledge'].update(publication_id='foreign'),
    lambda s: s['result']['brief']['project_state'].update(publication_id='foreign'),
    lambda s: s['result']['brief'].update(generated_at=NEXT),
    lambda s: s.update(generated_at=NEXT),
    lambda s: s['result'].pop('diagnostics'),
    lambda s: s.update(result=[]),
])
def test_incoherent_legacy_result_does_not_claim_available_history(engine, change):
    with Session(engine) as db:
        add_legacy_knowledge(db, change=change)
        view = knowledge_view(db)
        assert view['state'] == 'not_generated' and view['history_at'] is None


@pytest.mark.parametrize('timestamp', ['not-a-date', '2026-99-99T00:00:00Z', '20261117T000000', '',
                                       '2026-11-17T00:00:00+00:99', '2026-11-17T00:00:00+24:00'])
def test_invalid_legacy_history_timestamp_does_not_break_summary_dates(engine, timestamp):
    with Session(engine) as db:
        add_legacy_knowledge(db, timestamp=timestamp)
        assert knowledge_view(db)['history_at'] is None


def test_legacy_summary_reads_only_scalar_metadata_and_never_activates_or_returns_payload(engine):
    huge = 'PRIVATE_HISTORY_BODY ' * 100000
    with Session(engine) as db:
        add_legacy_knowledge(db, payload=huge)
    statements = []
    def observed(connection, cursor, statement, *args):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', observed)
    try:
        with Session(engine) as db:
            view = knowledge_view(db)
            assert view['state'] == 'earlier_result'
            assert db.get(ProjectKnowledge, 1) is None
    finally:
        event.remove(engine, 'before_cursor_execute', observed)
    assert not any(sql.startswith(('INSERT', 'UPDATE', 'DELETE')) for sql in statements)
    assert not any('generation_revisions.snapshot \n' in sql for sql in statements)
    assert huge not in str(view)


@pytest.fixture
def engine(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')]); db.commit()
    yield engine
    engine.dispose()


def latest(engine, pid=1, kind='brief'):
    with Session(engine) as db:
        return attempts.read_latest(db, pid, kind)


def test_attempts_persist_latest_failure_without_touching_saved_result(engine):
    with Session(engine) as db:
        db.add(ProjectBrief(project_id=1, generated_at='earlier', sections={}, sources=[], coverage=[])); db.commit()
    attempts.start(engine, 1, 'brief', START)
    attempts.finish(engine, 1, 'brief', START, state='failed', error='Codex did not finish generation within 600 seconds.')
    result = latest(engine)
    assert result['state'] == 'failed' and result['error_category'] == 'timeout'
    assert result['finished_at'] and result['saves_result'] is True
    with Session(engine) as db:
        assert db.get(ProjectBrief, 1).generated_at == 'earlier'
        row = attempts.generation_summary(db, 1)['views'][0]
        assert row['state'] == 'timed_out' and row['retryable'] and row['showing_previous']
        assert row['saved_at'] == 'earlier'


def test_failed_generation_restores_bounded_progress_after_restart(engine):
    from backend.app.integrations.codex import progress

    progress.begin(1, 'knowledge', engine=engine)
    progress.update(1, stage='Summarizing overview batches', batch_phase='overview synthesis',
                    batch_completed=9, batch_total=17, cache_hits=8)
    progress.record_diagnostics(1, {'model_requests': 12, 'private_data': ['source excerpt']})
    progress.finish(1, 'The selected account reached its usage limit.')
    restarted = create_engine(engine.url)
    try:
        status = progress.read(1, 'knowledge', engine=restarted)
        details = status['latest_attempt']['progress']
        assert status['state'] == 'failed'
        assert status['last_stage'] == details['stage'] == 'Summarizing overview batches'
        assert status['batch_phase'] == details['batch_phase'] == 'overview synthesis'
        assert status['batch_completed'] == 9 and status['batch_total'] == 17 and status['cache_hits'] == 8
        assert status['elapsed_seconds'] == details['elapsed_seconds']
        assert details['model_requests'] == 12 and 'private_data' not in details
        with Session(restarted) as db:
            view = next(item for item in attempts.generation_summary(db, 1)['views'] if item['kind'] == 'knowledge')
            assert view['latest_attempt']['progress'] == details
        progress.begin(1, 'knowledge', engine=restarted)
        try:
            assert latest(restarted, kind='knowledge')['progress'] is None
        finally:
            progress.finish(1, 'Finish fixture.')
    finally:
        restarted.dispose()


@pytest.mark.parametrize('error,is_timeout', [
    ('AI generation exceeded its overall time budget.', True),
    ('Model request timed out.', True),
    ('Model timeout.', True),
    ('The request deadline expired.', True),
    ('Generation budget must be an integer.', False),
    ('Exceeded context budget.', False),
    ('Exceeded input budget.', False),
    ('Exceeded token budget.', False),
    ('Unsupported source input.', False),
])
def test_timeout_classification_is_specific(engine, error, is_timeout):
    attempts.start(engine, 1, 'brief', START)
    attempts.finish(engine, 1, 'brief', START, state='failed', error=error)
    assert (latest(engine)['error_category'] == 'timeout') is is_timeout


def test_stale_run_cannot_replace_or_finish_a_newer_attempt(engine):
    attempts.start(engine, 1, 'brief', NEXT)
    attempts.start(engine, 1, 'brief', START)
    attempts.finish(engine, 1, 'brief', START, state='failed', error='Late old error')
    assert latest(engine)['started_at'] == NEXT and latest(engine)['state'] == 'running'
    attempts.finish(engine, 1, 'brief', NEXT, state='completed')
    attempts.finish(engine, 1, 'brief', NEXT, state='failed', error='Late cleanup')
    assert latest(engine)['state'] == 'completed'


def test_outcome_can_commit_or_rollback_with_its_saved_artifact(engine):
    attempts.start(engine, 1, 'parameters', START)
    with Session(engine) as db:
        db.add(GenerationRevision(project_id=1, kind='parameters', generated_at='new', snapshot={}))
        attempts.finish(engine, 1, 'parameters', START, state='completed', db=db)
        db.rollback()
    assert latest(engine, kind='parameters')['state'] == 'running'
    with Session(engine) as db:
        assert db.scalar(select(GenerationRevision.id)) is None
        db.add(GenerationRevision(project_id=1, kind='parameters', generated_at='new', snapshot={}))
        attempts.finish(engine, 1, 'parameters', START, state='completed', db=db)
        db.commit()
    assert latest(engine, kind='parameters')['state'] == 'completed'


def test_restart_marks_running_and_waiting_interrupted_but_keeps_terminal_results(engine):
    attempts.start(engine, 1, 'brief', START)
    token = attempts.queue_dependency(engine, 1)
    assert latest(engine, kind='scheme')['state'] == 'waiting'
    attempts.start(engine, 2, 'qa', START)
    attempts.finish(engine, 2, 'qa', START, state='cancelled')
    attempts.interrupt_running(engine)
    assert latest(engine)['state'] == latest(engine, kind='scheme')['state'] == 'interrupted'
    assert latest(engine, 2, 'qa')['state'] == 'cancelled'
    assert latest(engine, kind='scheme')['started_at'] == token
    assert 'restarted' in latest(engine)['error']


def test_dependency_tokens_do_not_overwrite_active_or_replacement_scheme(engine):
    token = attempts.queue_dependency(engine, 1)
    assert token and attempts.queue_dependency(engine, 1) is None
    attempts.finish(engine, 1, 'scheme', token, state='skipped', error='Knowledge failed')
    second = attempts.queue_dependency(engine, 1)
    assert second
    from backend.app.models import utcnow
    started = utcnow()
    attempts.start(engine, 1, 'scheme', started)
    attempts.finish(engine, 1, 'scheme', second, state='skipped', error='Stale dependency')
    assert latest(engine, kind='scheme')['state'] == 'running'
    assert latest(engine, kind='scheme')['started_at'] == started
    assert attempts.queue_dependency(engine, 1) is None


def test_project_ownership_and_cascade_are_enforced(engine):
    with pytest.raises(HTTPException) as error:
        attempts.start(engine, 999, 'brief', START)
    assert error.value.status_code == 404
    attempts.start(engine, 1, 'brief', START)
    assert latest(engine, 2) is None
    with Session(engine) as db:
        db.execute(delete(Project).where(Project.id == 1)); db.commit()
        assert db.scalar(select(ProjectGenerationAttempt.project_id)) is None


def test_summary_handles_missing_and_saved_views_without_loading_artifact_json(engine):
    huge = 'private payload ' * 100000
    with Session(engine) as db:
        db.add_all([
            ProjectBrief(project_id=1, generated_at='overview', sections={'payload': huge}, sources=[], coverage=[]),
            ProjectKnowledge(project_id=1, snapshot={'generated_at': 'knowledge', 'items': [huge]}),
            ProjectScheme(project_id=1, snapshot={'generated_at': 'scheme', 'payload': huge}),
            ProjectQA(project_id=2, generated_at='foreign', items=[], sources=[]),
        ]); db.commit()
    statements = []
    def observed(connection, cursor, statement, *args):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', observed)
    try:
        with Session(engine) as db:
            result = attempts.generation_summary(db, 1)
    finally:
        event.remove(engine, 'before_cursor_execute', observed)
    assert result['project_id'] == 1 and [row['kind'] for row in result['views']] == list(attempts.KINDS)
    by_kind = {row['kind']: row for row in result['views']}
    assert by_kind['brief']['state'] == by_kind['knowledge']['state'] == by_kind['scheme']['state'] == 'saved'
    assert by_kind['qa']['state'] == by_kind['parameters']['state'] == 'not_generated'
    assert by_kind['qa']['saved_at'] is None and by_kind['qa']['latest_attempt'] is None
    assert by_kind['qa']['retryable'] and not by_kind['knowledge']['retryable']
    assert all('SELECT project_knowledge.snapshot \n' not in sql for sql in statements)
    assert huge not in str(result)


def test_missing_artifact_and_live_failure_are_not_silently_marked_saved(engine):
    attempts.start(engine, 1, 'brief', START)
    attempts.finish(engine, 1, 'brief', START, state='completed')
    with Session(engine) as db:
        assert attempts.generation_summary(db, 1)['views'][0]['state'] == 'not_generated'
        db.add(ProjectBrief(project_id=1, generated_at='earlier', sections={}, sources=[], coverage=[])); db.commit()
        result = attempts.generation_summary(db, 1, live={'brief': {
            'state': 'failed', 'started_at': NEXT, 'error': 'Generation timed out'}})['views'][0]
    assert result['state'] == 'timed_out' and result['retryable'] and result['showing_previous']


def test_summary_and_status_endpoints_are_read_only_project_scoped(engine, monkeypatch):
    from backend.app.integrations.codex import router as routes
    attempts.start(engine, 1, 'qa', START)
    attempts.finish(engine, 1, 'qa', START, state='cancelled')
    def no_model(*args):
        raise AssertionError('Status browsing cannot invoke AI')
    monkeypatch.setattr(routes, 'manager_for', no_model)
    app = FastAPI(); app.state.engine = engine; app.include_router(routes.router)
    with TestClient(app) as client:
        result = client.get('/api/projects/1/codex/generation/summary')
        assert result.status_code == 200, result.text
        assert next(row for row in result.json()['views'] if row['kind'] == 'qa')['state'] == 'cancelled'
        assert client.get('/api/projects/999/codex/generation/summary').status_code == 404
        foreign = client.get('/api/projects/2/codex/generation/summary').json()
        assert all(row['latest_attempt'] is None for row in foreign['views'])
        state = client.get('/api/projects/1/codex/generation/status?kind=qa').json()
        assert state['state'] == 'cancelled' and state['latest_attempt']['started_at'] == START


def test_schema_upgrade_preserves_existing_saved_results(tmp_path):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text
    database = tmp_path / 'projecthub.sqlite3'
    old = create_engine('sqlite:///' + str(database))
    cfg = Config(); cfg.set_main_option('script_location', str(Path(__file__).resolve().parents[1] / 'migrations'))
    with old.begin() as connection:
        cfg.attributes['connection'] = connection
        command.upgrade(cfg, '0021')
    with Session(old) as db:
        db.add(Project(id=1, name='Existing')); db.flush()
        db.add(ProjectBrief(project_id=1, generated_at='saved-before-migration',
                            sections={'overview': ['Original content']}, sources=[], coverage=[])); db.commit()
    old.dispose()
    upgraded, _ = initialize(tmp_path)
    try:
        with Session(upgraded) as db:
            assert db.scalar(text('SELECT version_num FROM alembic_version')) == ScriptDirectory.from_config(cfg).get_current_head()
            assert db.get(ProjectBrief, 1).sections == {'overview': ['Original content']}
            assert attempts.read_latest(db, 1, 'brief') is None
            summary = attempts.generation_summary(db, 1)['views'][0]
            assert summary['state'] == 'saved' and summary['saved_at'] == 'saved-before-migration'
    finally:
        upgraded.dispose()


def test_legacy_saved_artifact_without_timestamp_is_not_missing(engine):
    with Session(engine) as db:
        db.add(ProjectKnowledge(project_id=1, snapshot={'project_id': 1, 'items': []})); db.commit()
        knowledge = next(row for row in attempts.generation_summary(db, 1)['views'] if row['kind'] == 'knowledge')
        assert knowledge['state'] == 'saved' and knowledge['saved_at'] is None
        assert not knowledge['retryable']
    attempts.start(engine, 1, 'knowledge', START)
    attempts.finish(engine, 1, 'knowledge', START, state='failed', error='A model request timed out')
    with Session(engine) as db:
        knowledge = next(row for row in attempts.generation_summary(db, 1)['views'] if row['kind'] == 'knowledge')
        assert knowledge['state'] == 'timed_out' and knowledge['showing_previous']


def test_summary_does_not_mix_an_artifact_read_with_a_later_completed_attempt(engine):
    with engine.connect() as connection:
        connection.exec_driver_sql('PRAGMA journal_mode=WAL')
    committed = False

    def save_between_queries(connection, cursor, statement, parameters, context, many):
        nonlocal committed
        if committed or 'FROM project_scheme' not in statement:
            return
        assert connection.connection.driver_connection.in_transaction
        committed = True
        with Session(engine) as writer:
            writer.add(ProjectKnowledge(project_id=1, snapshot={
                'project_id': 1, 'generated_at': NEXT, 'publication_id': 'new-version', 'items': []}))
            writer.add(ProjectGenerationAttempt(project_id=1, kind='knowledge',
                started_at=START, finished_at=NEXT, state='completed', saves_result=True))
            writer.commit()

    event.listen(engine, 'before_cursor_execute', save_between_queries)
    try:
        with Session(engine) as reader:
            before = knowledge_view(reader)
            assert committed
            assert before['state'] == 'not_generated' and before['latest_attempt'] is None
            assert before['saved_at'] is None
            # The scoped snapshot must end at return, even if the HTTP caller
            # or another reader reuses its Session for a later status check.
            after = knowledge_view(reader)
            assert after['state'] == 'saved' and after['saved_at'] == NEXT
            assert after['latest_attempt']['state'] == 'completed'
    finally:
        event.remove(engine, 'before_cursor_execute', save_between_queries)


def test_summary_does_not_commit_a_callers_pending_artifact(engine):
    with Session(engine) as writer:
        writer.add(ProjectKnowledge(project_id=1, snapshot={
            'project_id': 1, 'generated_at': NEXT, 'items': []}))
        assert knowledge_view(writer)['state'] == 'saved'
        assert writer.connection().connection.driver_connection.in_transaction
        with Session(engine) as observer:
            assert observer.get(ProjectKnowledge, 1) is None
        writer.rollback()
    with Session(engine) as observer:
        assert observer.get(ProjectKnowledge, 1) is None


def test_summary_preserves_an_existing_physical_read_transaction(engine):
    with Session(engine) as reader:
        reader.connection().exec_driver_sql('BEGIN')
        assert knowledge_view(reader)['state'] == 'not_generated'
        assert reader.connection().connection.driver_connection.in_transaction
        reader.rollback()
