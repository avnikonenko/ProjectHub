"""Successful preview recovery is independent of published knowledge and failures."""
from importlib import import_module

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.app.models import GenerationRevision
from backend.app.integrations.codex import knowledge_brief as pilot, progress
from backend.app.integrations.codex.knowledge_previews import (
    PREVIEW_KIND, read_knowledge_preview, stage_knowledge_preview,
)
from backend.app.integrations.codex.protocol import CodexError
from backend.tests.integrations.test_knowledge_brief import prepared, FakeSynthesis, _stored


def _preview(project_id=1, identity='preview-one'):
    return {'published': False, 'publication_id': identity,
            'knowledge': {'project_id': project_id, 'publication_id': identity,
                          'generated_at': identity, 'items': []},
            'brief': {'generated_at': identity, 'project_state': {'publication_id': identity}},
            'baseline_brief': None, 'diagnostics': {'duration_seconds': 1.5}}


def _latest(engine, project_id=1):
    with Session(engine) as db:
        return read_knowledge_preview(db, project_id)


def test_successful_preview_is_recoverable_without_changing_published_pair(prepared):
    before = _stored(prepared.engine)
    result = pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis())
    assert _latest(prepared.engine) == result
    assert _stored(prepared.engine) == before
    assert _latest(prepared.engine, 2) is None
    with Session(prepared.engine) as db:
        row = db.scalar(select(GenerationRevision).where(GenerationRevision.kind == PREVIEW_KIND))
        assert row.generated_at == result['brief']['generated_at'] == row.snapshot['generated_at']


def test_synthesis_failure_preserves_last_successful_preview(prepared):
    previous = pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis())
    before = _stored(prepared.engine)
    manager = FakeSynthesis()
    manager.fail = True
    with pytest.raises(CodexError, match='Mock synthesis unavailable'):
        pilot.generate_knowledge_brief(prepared.engine, 1, manager)
    assert _latest(prepared.engine) == previous
    assert _stored(prepared.engine) == before


def test_cancellation_before_preview_commit_preserves_previous_preview(prepared, monkeypatch):
    previous = pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis())
    before = _stored(prepared.engine)
    original = pilot.stage_knowledge_preview

    def cancelling_stage(db, project_id, result):
        original(db, project_id, result)
        progress.cancel(project_id, 'knowledge', progress.read(project_id, 'knowledge')['started_at'])

    monkeypatch.setattr(pilot, 'stage_knowledge_preview', cancelling_stage)
    with pytest.raises(progress.GenerationCancelled):
        pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis())
    assert _latest(prepared.engine) == previous
    assert _stored(prepared.engine) == before
    with Session(prepared.engine) as db:
        assert len(db.scalars(select(GenerationRevision).where(GenerationRevision.kind == PREVIEW_KIND)).all()) == 1


def test_preview_commit_failure_preserves_previous_preview_and_published_pair(prepared):
    previous = pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis())
    before = _stored(prepared.engine)
    with prepared.engine.begin() as connection:
        connection.execute(text("CREATE TRIGGER fail_preview BEFORE INSERT ON generation_revisions "
                                "WHEN NEW.kind='knowledge_preview' "
                                "BEGIN SELECT RAISE(ABORT, 'injected preview failure'); END"))
    with pytest.raises(IntegrityError, match='injected preview failure'):
        pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis())
    assert _latest(prepared.engine) == previous
    assert _stored(prepared.engine) == before


def test_changed_sources_do_not_replace_last_successful_preview(prepared, monkeypatch):
    previous = pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis())
    before = _stored(prepared.engine)
    original = pilot.load_sources
    calls = 0

    def changing_sources(*args, **kwargs):
        nonlocal calls
        calls += 1
        bundle = original(*args, **kwargs)
        if calls > 1:
            bundle['manifest']['fingerprint'] = 'changed'
        return bundle

    monkeypatch.setattr(pilot, 'load_sources', changing_sources)
    with pytest.raises(CodexError, match='Sources changed during preview'):
        pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis())
    assert _latest(prepared.engine) == previous
    assert _stored(prepared.engine) == before


def test_latest_successful_preview_wins_and_returned_data_is_detached(prepared):
    with Session(prepared.engine) as db:
        stage_knowledge_preview(db, 1, _preview(identity='first'))
        stage_knowledge_preview(db, 1, _preview(identity='second'))
        stage_knowledge_preview(db, 2, _preview(2, 'other-project'))
        db.commit()
        result = read_knowledge_preview(db, 1)
        assert result['publication_id'] == 'second'
        result['knowledge']['items'].append('caller-only mutation')
        assert read_knowledge_preview(db, 1) == _preview(identity='second')
        assert read_knowledge_preview(db, 2) == _preview(2, 'other-project')


@pytest.mark.parametrize('change', [
    lambda value: value['knowledge'].update(project_id=2),
    lambda value: value['brief']['project_state'].update(publication_id='foreign'),
    lambda value: value['knowledge'].update(generated_at='different'),
    lambda value: value.update(published=True),
])
def test_preview_cannot_be_saved_with_foreign_or_mismatched_identity(prepared, change):
    value = _preview()
    change(value)
    with Session(prepared.engine) as db:
        with pytest.raises(CodexError):
            stage_knowledge_preview(db, 1, value)
        assert read_knowledge_preview(db, 1) is None


def test_unknown_project_cannot_read_or_save_preview(prepared):
    with Session(prepared.engine) as db:
        with pytest.raises(CodexError, match='Project not found'):
            read_knowledge_preview(db, 999)
        with pytest.raises(CodexError, match='Project not found'):
            stage_knowledge_preview(db, 999, _preview(999))


@pytest.mark.parametrize('field', ['publication_id', 'generated_at'])
def test_blank_matching_identities_are_not_valid_preview_identities(prepared, field):
    value = _preview()
    value['knowledge'][field] = ' \t '
    if field == 'publication_id':
        value[field] = value['brief']['project_state'][field] = ' \t '
    else:
        value['brief'][field] = ' \t '
    with Session(prepared.engine) as db:
        with pytest.raises(CodexError, match='identities do not match'):
            stage_knowledge_preview(db, 1, value)
        assert read_knowledge_preview(db, 1) is None


def test_preview_get_recovers_after_restart_and_enforces_project_scope(tmp_path, monkeypatch):
    router = import_module('backend.app.integrations.codex.router')
    monkeypatch.setattr(router, 'manager_for', lambda *args: pytest.fail('Read-only recovery invoked Codex'))
    path = tmp_path / 'preview-data'
    app = create_app(path)
    value = _preview()
    with TestClient(app) as client:
        first = client.post('/api/projects', json={'name': 'First'}).json()['id']
        second = client.post('/api/projects', json={'name': 'Second'}).json()['id']
        assert (first, second) == (1, 2)
        assert client.get('/api/projects/1/codex/knowledge/preview').json() is None
        with Session(app.state.engine) as db:
            stage_knowledge_preview(db, 1, value)
            db.commit()
    app.state.engine.dispose()
    restarted = create_app(path)
    try:
        with TestClient(restarted) as client:
            response = client.get('/api/projects/1/codex/knowledge/preview')
            assert response.status_code == 200 and response.json() == value
            assert client.get('/api/projects/2/codex/knowledge/preview').json() is None
            assert client.get('/api/projects/999/codex/knowledge/preview').status_code == 404
    finally:
        restarted.state.engine.dispose()
