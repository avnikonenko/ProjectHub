"""Explicit activation reuses stored output; semantic generation is mocked setup only."""
from copy import deepcopy
from importlib import import_module
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.app.models import Project, ProjectBrief
from backend.app.integrations.codex import knowledge_brief as pilot
from backend.app.integrations.codex.brief import BriefBusy, generation_lock
from backend.app.integrations.codex.knowledge_previews import (
    KnowledgePreviewConflict, read_knowledge_preview, stage_knowledge_preview,
)
from backend.tests.integrations.test_knowledge_brief import prepared, FakeSynthesis, _stored


@pytest.fixture
def saved(prepared):
    # This primary source stands behind FakeSynthesis's fixture interpretation.
    prepared.bundle['sources'] = [{'id': 'source-one', 'role': 'user', 'text': 'Use SQLite.'}]
    result = pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis())
    return prepared, result


def activate(saved):
    state, result = saved
    return pilot.publish_saved_knowledge_preview(state.engine, 1, result['publication_id'])


def test_saved_result_activates_matching_pair_without_regeneration_and_is_idempotent(saved, monkeypatch):
    state, result = saved
    before_other = _stored(state.engine, 2)
    monkeypatch.setattr(pilot, '_synthesize', lambda *a: pytest.fail('Activation invoked synthesis'))
    monkeypatch.setattr(pilot, 'build_candidate', lambda *a, **k: pytest.fail('Activation invoked extraction'))
    activated = activate(saved)
    knowledge, brief, revisions = _stored(state.engine)
    assert activated == {**result, 'published': True}
    assert knowledge == result['knowledge'] and brief == result['brief']
    assert [r[0] for r in revisions[-2:]] == ['knowledge', 'brief']
    before = _stored(state.engine)
    assert activate(saved) == activated
    assert _stored(state.engine) == before and _stored(state.engine, 2) == before_other
    with Session(state.engine) as db:
        assert read_knowledge_preview(db, 1) == result


def test_changed_primary_sources_preserve_both_published_records(saved):
    state, _ = saved
    before = _stored(state.engine)
    state.bundle['manifest']['fingerprint'] = 'changed-primary-sources'
    with pytest.raises(KnowledgePreviewConflict, match='sources changed'):
        activate(saved)
    assert _stored(state.engine) == before


def test_changed_overview_is_not_silently_overwritten(saved):
    state, _ = saved
    with Session(state.engine) as db:
        db.get(ProjectBrief, 1).sections = {'overview': [{'text': 'New current overview'}]}
        db.commit()
    before = _stored(state.engine)
    with pytest.raises(KnowledgePreviewConflict, match='overview changed'):
        activate(saved)
    assert _stored(state.engine) == before


def test_newer_preview_prevents_activating_an_older_result(saved):
    state, _ = saved
    before = _stored(state.engine)
    pilot.generate_knowledge_brief(state.engine, 1, FakeSynthesis())
    with pytest.raises(KnowledgePreviewConflict, match='selected saved result'):
        activate(saved)
    assert _stored(state.engine) == before


def test_failure_in_second_revision_rolls_back_all_publication_writes(saved):
    state, result = saved
    before = _stored(state.engine)
    with state.engine.begin() as connection:
        connection.execute(text("CREATE TRIGGER fail_saved_activation BEFORE INSERT ON generation_revisions "
                                "WHEN NEW.kind='brief' AND NEW.generated_at!='old' "
                                "BEGIN SELECT RAISE(ABORT, 'activation rollback'); END"))
    with pytest.raises(IntegrityError, match='activation rollback'):
        activate(saved)
    assert _stored(state.engine) == before
    with Session(state.engine) as db:
        assert read_knowledge_preview(db, 1) == result


@pytest.mark.parametrize('field', ['items', 'sources'])
def test_nested_foreign_project_data_is_rejected(saved, field):
    state, result = saved
    changed = deepcopy(result)
    changed['knowledge'][field][0]['project_id'] = 2
    with Session(state.engine) as db:
        stage_knowledge_preview(db, 1, changed)
        db.commit()
    before = _stored(state.engine)
    with pytest.raises(KnowledgePreviewConflict, match='does not belong'):
        activate(saved)
    assert _stored(state.engine) == before


def test_modified_literal_evidence_is_rejected_without_touching_published_pair(saved):
    state, result = saved
    changed = deepcopy(result)
    changed['knowledge']['items'][0]['evidence'][0]['excerpt'] = 'Invented acceptance.'
    with Session(state.engine) as db:
        stage_knowledge_preview(db, 1, changed)
        db.commit()
    before = _stored(state.engine)
    with pytest.raises(KnowledgePreviewConflict, match='no longer matches'):
        activate(saved)
    assert _stored(state.engine) == before


def test_older_no_repository_preview_needs_no_new_generation(saved):
    state, result = saved
    legacy = deepcopy(result)
    legacy.pop('publication_context')
    with Session(state.engine) as db:
        stage_knowledge_preview(db, 1, legacy)
        db.commit()
    assert activate(saved)['published'] is True


def test_repository_signature_required_and_checked(prepared, monkeypatch):
    prepared.bundle['sources'] = [{'id': 'source-one', 'role': 'user', 'text': 'Use SQLite.'}]
    prepared.bundle['project']['repository_path'] = '/fixture/repository'
    with Session(prepared.engine) as db:
        db.get(Project, 1).repository_path = '/fixture/repository'
        db.commit()
    repository = {'sources': [], 'coverage': ['Fixture repository inspection']}
    monkeypatch.setattr(pilot, 'collect_repository_code', lambda path: deepcopy(repository))
    result = pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis())
    before = _stored(prepared.engine)
    repository['coverage'].append('Changed repository')
    with pytest.raises(KnowledgePreviewConflict, match='Repository evidence changed'):
        pilot.publish_saved_knowledge_preview(prepared.engine, 1, result['publication_id'])
    assert _stored(prepared.engine) == before
    repository['coverage'].pop()
    legacy = deepcopy(result)
    legacy.pop('publication_context')
    with Session(prepared.engine) as db:
        stage_knowledge_preview(db, 1, legacy)
        db.commit()
    with pytest.raises(KnowledgePreviewConflict, match='no repository freshness'):
        pilot.publish_saved_knowledge_preview(prepared.engine, 1, result['publication_id'])
    assert _stored(prepared.engine) == before
    with Session(prepared.engine) as db:
        stage_knowledge_preview(db, 1, result)
        db.commit()
    assert pilot.publish_saved_knowledge_preview(prepared.engine, 1, result['publication_id'])['published']
    published = _stored(prepared.engine)
    repository['coverage'].append('Changed after activation')
    assert pilot.publish_saved_knowledge_preview(prepared.engine, 1, result['publication_id'])['published']
    assert _stored(prepared.engine) == published


def test_activation_rechecks_latest_preview_and_baseline_inside_writer_transaction(saved, monkeypatch):
    state, result = saved
    original = pilot.publish_pair
    def competing_write(*args, **kwargs):
        with Session(state.engine) as db:
            db.get(ProjectBrief, 1).sections = {'overview': [{'text': 'Concurrent overview'}]}
            db.commit()
        return original(*args, **kwargs)
    monkeypatch.setattr(pilot, 'publish_pair', competing_write)
    with pytest.raises(KnowledgePreviewConflict, match='overview changed'):
        activate(saved)
    assert _stored(state.engine)[0]['publication_id'] == 'old'


def test_activation_rechecks_preview_identity_inside_writer_transaction(saved, monkeypatch):
    state, result = saved
    original = pilot.publish_pair
    newer = deepcopy(result)
    newer['publication_id'] = newer['knowledge']['publication_id'] = 'newer-result'
    newer['brief']['project_state']['publication_id'] = 'newer-result'
    before = _stored(state.engine)
    def competing_preview(*args, **kwargs):
        with Session(state.engine) as db:
            stage_knowledge_preview(db, 1, newer)
            db.commit()
        return original(*args, **kwargs)
    monkeypatch.setattr(pilot, 'publish_pair', competing_preview)
    with pytest.raises(KnowledgePreviewConflict, match='newer saved result'):
        activate(saved)
    assert _stored(state.engine) == before


def test_activation_waits_for_generation_and_preserves_existing_result(saved):
    state, _ = saved
    before = _stored(state.engine)
    with generation_lock:
        with pytest.raises(BriefBusy, match='Another AI generation'):
            activate(saved)
    assert _stored(state.engine) == before


@pytest.mark.parametrize('change', [
    lambda result: result['brief'].update(sources=None),
    lambda result: result['brief'].update(project_map=[]),
    lambda result: result['brief'].update(sections=[]),
])
def test_malformed_saved_structures_are_rejected_before_writes(saved, change):
    state, result = saved
    malformed = deepcopy(result)
    change(malformed)
    with Session(state.engine) as db:
        stage_knowledge_preview(db, 1, malformed)
        db.commit()
    before = _stored(state.engine)
    with pytest.raises(KnowledgePreviewConflict, match='incomplete'):
        activate(saved)
    assert _stored(state.engine) == before


def test_api_scopes_activation_and_never_constructs_codex_manager(saved, monkeypatch):
    state, result = saved
    router = import_module('backend.app.integrations.codex.router')
    monkeypatch.setattr(router, 'manager_for', lambda *args: pytest.fail('Activation constructed Codex manager'))
    app = create_app(Path(state.engine.url.database).parent)
    try:
        with TestClient(app) as client:
            body = {'publication_id': result['publication_id']}
            assert client.post('/api/projects/999/codex/knowledge/preview/publish', json=body).status_code == 404
            assert client.post('/api/projects/2/codex/knowledge/preview/publish', json=body).status_code == 409
            for invalid in ({}, {'publication_id': ' '}, {**body, 'knowledge': result['knowledge']}):
                assert client.post('/api/projects/1/codex/knowledge/preview/publish', json=invalid).status_code == 422
            response = client.post('/api/projects/1/codex/knowledge/preview/publish', json=body)
            assert response.status_code == 200 and response.json()['published'] is True
            assert response.json()['publication_id'] == body['publication_id']
    finally:
        app.state.engine.dispose()
