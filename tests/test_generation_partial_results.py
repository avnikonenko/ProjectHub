"""Recoverable result gaps remain visible across workspace reloads."""
import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import generation_attempts as attempts
from backend.app.models import Project, ProjectBrief, ProjectKnowledge, ProjectQA, ProjectScheme

SAVED = '2026-11-19T12:00:00Z'
RETRY = '2026-11-19T13:00:00Z'
FINAL = '2026-11-19T14:00:00Z'


@pytest.fixture
def engine(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(id=1, name='Partial results'), Project(id=2, name='Other project')])
        db.commit()
    yield engine
    engine.dispose()


def views(db, pid=1):
    return {row['kind']: row for row in attempts.generation_summary(db, pid)['views']}


def test_saved_archive_omissions_and_qa_fallback_are_retryable(engine):
    omissions = [{'phase': 'history', 'batch': 4, 'source_count': 1, 'sources': ['Session']}]
    with Session(engine) as db:
        db.add_all([
            ProjectBrief(project_id=1, generated_at=SAVED, sections={}, sources=[], coverage=[],
                         generation={'diagnostics': {'archive_omissions': omissions}}),
            ProjectKnowledge(project_id=1, snapshot={'generated_at': SAVED, 'diagnostics': {'invalid_evidence_items': 2}}),
            ProjectQA(project_id=1, generated_at=SAVED, items=[], sources=[],
                      generation={'diagnostics': {'qa_rank_fallback_batches': 1}}),
            ProjectScheme(project_id=1, snapshot={'generated_at': SAVED, 'coverage': {'partial': True},
                                                  'diagnostics': {'archive_omissions': []}}),
        ])
        db.commit()

    with Session(engine) as db:
        result = views(db)
        for kind in ('brief', 'knowledge', 'qa'):
            assert result[kind]['state'] == 'partial'
            assert result[kind]['saved_at'] == SAVED and not result[kind]['showing_previous']
            assert result[kind]['retryable']
        assert result['scheme']['state'] == 'saved' and not result['scheme']['retryable']
        assert all(view['state'] == 'not_generated' for view in views(db, 2).values())


def test_only_explicit_positive_diagnostics_mark_recoverable_gaps(engine):
    with Session(engine) as db:
        db.add_all([
            ProjectKnowledge(project_id=1, snapshot={'generated_at': SAVED, 'diagnostics': {
                'invalid_evidence_items': '2', 'unsupported_agreement_items': True}}),
            ProjectQA(project_id=1, generated_at=SAVED, items=[], sources=[],
                      generation={'diagnostics': {'qa_rank_fallback_batches': 0, 'qa_consolidate_fallback_batches': 2}}),
            ProjectScheme(project_id=1, snapshot={'generated_at': SAVED, 'diagnostics': {
                'archive_omissions': [{'phase': 'conversation_overviews_v1'}]}}),
        ])
        db.commit()
        result = views(db)
        assert result['knowledge']['state'] == 'saved'
        assert result['qa']['state'] == result['scheme']['state'] == 'partial'


def test_new_attempt_takes_precedence_over_partial_knowledge_until_saved(engine):
    with Session(engine) as db:
        db.add(ProjectKnowledge(project_id=1, snapshot={'generated_at': SAVED, 'diagnostics': {
            'unsupported_agreement_items': 1}}))
        db.commit()
        assert views(db)['knowledge']['state'] == 'partial'
    attempts.start(engine, 1, 'knowledge', RETRY)
    with Session(engine) as db:
        active = views(db)['knowledge']
        assert active['state'] == 'running' and active['showing_previous'] and not active['retryable']
    attempts.finish(engine, 1, 'knowledge', RETRY, state='failed', error='Retry failed')
    with Session(engine) as db:
        failed = views(db)['knowledge']
        assert failed['state'] == 'failed' and failed['showing_previous'] and failed['retryable']
    attempts.start(engine, 1, 'knowledge', FINAL)
    with Session(engine) as db:
        db.get(ProjectKnowledge, 1).snapshot = {'generated_at': FINAL, 'diagnostics': {}}
        attempts.finish(engine, 1, 'knowledge', FINAL, state='completed', db=db)
        db.commit()
        saved = views(db)['knowledge']
        assert saved['state'] == 'saved' and saved['saved_at'] == FINAL and not saved['retryable']
