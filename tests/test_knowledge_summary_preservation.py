"""Legacy summaries survive removal of their backing Overview data atomically."""
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, ProjectBrief, ProjectKnowledge, GenerationRevision
from backend.app.integrations.codex.knowledge import read_knowledge, preserve_legacy_knowledge_summary
from backend.app.integrations.codex import router as routes


def legacy_summary():
    return {'generated_at': 'old', 'sections': {'overview': []}, 'sources': [],
            'coverage': [], 'generation': None, 'project_map': None,
            'project_state': {'publication_id': 'publication', 'pipeline': 'knowledge-v1'}}


@pytest.fixture
def legacy(tmp_path):
    engine, _ = initialize(tmp_path)
    snapshot = {'project_id': 1, 'publication_id': 'publication', 'generated_at': 'old',
                'items': [], 'sources': [], 'topic_groups': [],
                'topic_grouping': {'saved': 'tag-metadata'}}
    summary = legacy_summary()
    general = {**summary, 'generated_at': 'new', 'project_state': {'pipeline': 'general'}}
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')]); db.flush()
        db.add(ProjectKnowledge(project_id=1, snapshot=deepcopy(snapshot)))
        db.add(ProjectBrief(project_id=1, **general))
        own = GenerationRevision(project_id=1, kind='brief', generated_at='old', snapshot=summary)
        foreign = GenerationRevision(project_id=2, kind='brief', generated_at='old', snapshot=summary)
        db.add_all([own, foreign]); db.flush(); ids = own.id, foreign.id; db.commit()
    app = FastAPI(); app.state.engine = engine; app.include_router(routes.router)
    yield engine, app, snapshot, summary, ids
    engine.dispose()


def test_preservation_only_stages_storage_and_does_not_revise_publication(legacy):
    engine, _, original, summary, _ = legacy
    with Session(engine) as db:
        assert preserve_legacy_knowledge_summary(db, 1)
        assert db.get(ProjectKnowledge, 1).snapshot == {**original, 'summary': summary}
        # The caller has not committed; closing this session rolls back.
    with Session(engine) as db:
        assert db.get(ProjectKnowledge, 1).snapshot == original
        revision_count = db.scalar(select(func.count()).select_from(GenerationRevision))
        assert preserve_legacy_knowledge_summary(db, 1)
        db.commit()
    with Session(engine) as db:
        assert db.get(ProjectKnowledge, 1).snapshot == {**original, 'summary': summary}
        assert not preserve_legacy_knowledge_summary(db, 1)
        assert db.scalar(select(func.count()).select_from(GenerationRevision)) == revision_count


def test_deleting_backing_revision_preserves_summary_and_tags(legacy):
    engine, app, original, summary, (revision_id, _) = legacy
    with TestClient(app) as client:
        response = client.delete(f'/api/projects/1/codex/revisions/{revision_id}',
                                 params={'expected_generated_at': 'old'})
        assert response.status_code == 204
    with Session(engine) as db:
        assert db.get(GenerationRevision, revision_id) is None
        assert db.get(ProjectKnowledge, 1).snapshot == {**original, 'summary': summary}
        assert read_knowledge(db, 1)['summary'] == summary
        assert db.get(ProjectBrief, 1).generated_at == 'new'
        assert db.scalar(select(func.count()).select_from(GenerationRevision).where(
            GenerationRevision.project_id == 1, GenerationRevision.kind == 'knowledge')) == 0


def test_foreign_and_current_revision_rejections_do_not_materialize_summary(legacy):
    engine, app, original, summary, (revision_id, foreign_id) = legacy
    with Session(engine) as db:
        db.get(ProjectBrief, 1).generated_at = 'old'; db.commit()
    with TestClient(app) as client:
        assert client.delete(f'/api/projects/1/codex/revisions/{foreign_id}').status_code == 404
        assert client.delete(f'/api/projects/1/codex/revisions/{revision_id}',
                             params={'expected_generated_at': 'old'}).status_code == 409
    with Session(engine) as db:
        assert db.get(ProjectKnowledge, 1).snapshot == original
        assert db.get(GenerationRevision, revision_id).snapshot == summary


@pytest.mark.parametrize('change', [
    {'generated_at': 'wrong'}, {'project_id': 2}, {'sections': []},
    {'sections': {'overview': 'not records'}}, {'sources': [{'project_id': 2}]},
    {'project_state': {'publication_id': 'foreign', 'pipeline': 'knowledge-v1'}},
])
def test_malformed_or_unrelated_summary_is_not_copied(legacy, change):
    engine, _, original, summary, (revision_id, _) = legacy
    with Session(engine) as db:
        db.get(GenerationRevision, revision_id).snapshot = {**summary, **change}; db.commit()
    with Session(engine) as db:
        assert not preserve_legacy_knowledge_summary(db, 1)
        assert db.get(ProjectKnowledge, 1).snapshot == original


def test_delete_failure_rolls_back_summary_copy_and_revision_delete(legacy):
    engine, app, original, summary, (revision_id, _) = legacy
    writes = []

    def fail_delete(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith('UPDATE project_knowledge'):
            writes.append('summary-staged')
        if statement.startswith('DELETE FROM generation_revisions'):
            raise RuntimeError('Fixture delete failure')

    event.listen(engine, 'before_cursor_execute', fail_delete)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            assert client.delete(f'/api/projects/1/codex/revisions/{revision_id}',
                                 params={'expected_generated_at': 'old'}).status_code == 500
    finally:
        event.remove(engine, 'before_cursor_execute', fail_delete)
    assert writes == ['summary-staged']
    with Session(engine) as db:
        assert db.get(ProjectKnowledge, 1).snapshot == original
        assert db.get(GenerationRevision, revision_id).snapshot == summary
