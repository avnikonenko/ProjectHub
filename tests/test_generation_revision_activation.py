"""Historical result activation through HTTP with a real project database."""

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import router as routes
from backend.app.integrations.codex.revisions import save_revision
from backend.app.integrations.codex.tag_generation import TagGenerationConflict, generate_tags
from backend.app.models import (
    GenerationRevision, Project, ProjectBrief, ProjectGenerationAttempt,
    ProjectKnowledge, ProjectQA, ProjectScheme,
)


OLDER = '2026-10-01T12:00:00Z'
NEWER = '2026-10-03T12:00:00Z'
FUTURE = '2026-10-05T12:00:00Z'
BASE = '/api/projects/1/codex'


@pytest.fixture
def application(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')])
        db.commit()
    app = FastAPI()
    app.state.engine = engine
    app.include_router(routes.router)
    try:
        with TestClient(app) as client:
            yield client, engine
    finally:
        engine.dispose()


def _revisions(db, kind, *snapshots, project_id=1):
    rows = [GenerationRevision(project_id=project_id, kind=kind, generated_at=snapshot['generated_at'],
                               snapshot=snapshot) for snapshot in snapshots]
    db.add_all(rows)
    db.flush()
    return [row.id for row in rows]


def _history(client, kind):
    response = client.get(f'{BASE}/revisions', params={'kind': kind})
    assert response.status_code == 200, response.text
    return response.json()


def _activate(client, revision_id):
    return client.post(f'{BASE}/revisions/{revision_id}/activate')


def _overview(generated_at, message):
    return dict(generated_at=generated_at, sections={'overview': [{'text': message, 'source_ids': []}]},
                sources=[], coverage=[], project_map=None, generation={'model': message})


def test_overview_activation_preserves_both_generations_with_identical_timestamps(application):
    client, engine = application
    old, newer = _overview(OLDER, 'First'), _overview(OLDER, 'Second')
    with Session(engine) as db:
        old_id, newer_id = _revisions(db, 'brief', old, newer)
        db.add(ProjectBrief(project_id=1, **newer))
        db.commit()

    assert _history(client, 'brief')['active_id'] == newer_id
    for wanted, snapshot in [(old_id, old), (newer_id, newer)]:
        response = _activate(client, wanted)
        assert response.status_code == 200, response.text
        assert response.json() == {'kind': 'brief', 'snapshot': snapshot}
        assert client.get(f'{BASE}/brief').json() == snapshot
        history = _history(client, 'brief')
        assert history['active_id'] == wanted
        assert [row['id'] for row in history['results']] == [newer_id, old_id]
        assert client.delete(f'{BASE}/revisions/{wanted}').status_code == 409


def test_parameters_activation_persists_without_copying_revisions_or_changing_attempts(application, monkeypatch):
    client, engine = application
    old = {'project_id': 1, 'generated_at': OLDER, 'items': [], 'sources': [], 'fingerprint': 'old',
           'coverage': {'partial': False, 'limitations': []}}
    newer = {**old, 'generated_at': NEWER, 'fingerprint': 'new'}
    monkeypatch.setattr(routes, 'collect_parameter_evidence', lambda *_: newer)
    with Session(engine) as db:
        old_id, newer_id = _revisions(db, 'parameters', old, newer)
        db.add(ProjectGenerationAttempt(project_id=1, kind='parameters', state='completed',
                                        started_at=NEWER, finished_at=NEWER))
        db.commit()

    for _ in range(2):
        response = _activate(client, old_id)
        assert response.status_code == 200, response.text
        assert response.json() == {'kind': 'parameters', 'snapshot': old}
        current = client.get(f'{BASE}/parameters').json()
        assert current['snapshot'] == old and current['freshness']['state'] == 'changed'
        history = _history(client, 'parameters')
        assert history['active_id'] == old_id
        assert [row['id'] for row in history['results']] == [newer_id, old_id]
        summary = client.get(f'{BASE}/generation/summary').json()
        view = next(view for view in summary['views'] if view['kind'] == 'parameters')
        assert view['saved_at'] == OLDER
        assert view['latest_attempt']['started_at'] == NEWER

    next_snapshot = {**old, 'generated_at': FUTURE}
    with Session(engine) as db:
        save_revision(db, 1, 'parameters', next_snapshot)
        db.commit()
    assert client.get(f'{BASE}/parameters').json()['snapshot'] == next_snapshot
    history = _history(client, 'parameters')
    assert history['active_id'] == history['results'][0]['id']
    assert [row['id'] for row in history['results'][1:]] == [newer_id, old_id]


def test_qa_and_scheme_activate_their_own_saved_artifacts(application):
    client, engine = application
    old_qa = {'generated_at': OLDER, 'items': [], 'sources': [],
              'generation': {'model': 'First'}, 'topic_groups': [], 'topic_grouping': {},
              'coverage': ['Prior coverage'], 'diagnostics': {'model_requests': 4}}
    old_scheme = {'project_id': 1, 'generated_at': OLDER, 'coverage': {}, 'sources': [],
                  'knowledge_context': {'publication_id': 'historical-evidence'}}
    tags = [{'id': 'tag', 'title': 'Retained group', 'item_ids': []}]
    with Session(engine) as db:
        qa_id, = _revisions(db, 'qa', old_qa)
        scheme_id, = _revisions(db, 'scheme', old_scheme)
        db.add_all([ProjectQA(project_id=1, generated_at=NEWER, items=[], sources=[],
                              generation={'model': 'Most recent', 'topic_groups': tags}),
                    ProjectScheme(project_id=1, snapshot={**old_scheme, 'generated_at': NEWER})])
        db.commit()

    for kind, revision_id, snapshot in [('qa', qa_id, old_qa), ('scheme', scheme_id, old_scheme)]:
        response = _activate(client, revision_id)
        assert response.status_code == 200, response.text
        if kind == 'qa':
            snapshot = {**snapshot, 'revision_id': revision_id}
        assert response.json() == {'kind': kind, 'snapshot': snapshot}
        assert _history(client, kind)['active_id'] == revision_id
    assert client.get(f'{BASE}/qa').json() == {**old_qa, 'revision_id': qa_id}
    with Session(engine) as db:
        assert db.get(ProjectScheme, 1).snapshot == old_scheme
        archived_qa = db.scalar(select(GenerationRevision).where(
            GenerationRevision.project_id == 1, GenerationRevision.kind == 'qa',
            GenerationRevision.generated_at == NEWER))
        assert archived_qa.snapshot['topic_groups'] == tags


def test_same_timestamp_qa_mutations_require_the_active_revision(application, monkeypatch):
    client, engine = application
    question = 'Which revision is active?'
    old = {'generated_at': OLDER, 'items': [{'question': question, 'answer': 'Old answer', 'source_ids': []}],
           'sources': [], 'generation': None}
    new = {**old, 'items': [{**old['items'][0], 'answer': 'New answer'}]}
    with Session(engine) as db:
        old_id, new_id = _revisions(db, 'qa', old, new)
        db.add(ProjectQA(project_id=1, **new))
        db.commit()

    stale = client.get(f'{BASE}/qa').json()
    assert stale['revision_id'] == new_id
    assert _activate(client, old_id).json()['snapshot']['revision_id'] == old_id

    params = {'question': question, 'generated_at': stale['generated_at']}
    for revision in ({}, {'revision_id': stale['revision_id']}):
        assert client.delete(f'{BASE}/qa/items', params={**params, **revision}).status_code == 409
        with pytest.raises(TagGenerationConflict, match='selected Q&A changed'):
            generate_tags(engine, 1, object(), target='qa', generated_at=OLDER, **revision)
    assert client.get(f'{BASE}/qa').json()['items'][0]['answer'] == 'Old answer'

    group_records = 'backend.app.integrations.codex.tag_generation.group_records'
    grouping = {'topic_groups': [], 'topic_grouping': {}}

    def switch_during_generation(*_args, **_kwargs):
        assert _activate(client, new_id).status_code == 200
        return grouping

    monkeypatch.setattr(group_records, switch_during_generation)
    with pytest.raises(TagGenerationConflict, match='selected Q&A changed'):
        generate_tags(engine, 1, object(), target='qa', generated_at=OLDER, revision_id=old_id)
    assert client.get(f'{BASE}/qa').json()['revision_id'] == new_id

    assert _activate(client, old_id).status_code == 200
    monkeypatch.setattr(group_records, lambda *_args, **_kwargs: grouping)
    tagged = generate_tags(engine, 1, object(), target='qa', generated_at=OLDER, revision_id=old_id)
    assert tagged['revision_id'] == old_id
    response = client.delete(f'{BASE}/qa/items', params={**params, 'revision_id': old_id})
    assert response.status_code == 200, response.text
    assert response.json()['revision_id'] == old_id
    assert response.json()['items'] == []
    assert client.get(f'{BASE}/revisions/{new_id}').json()['snapshot'] == new


def test_activation_rolls_back_current_artifact_if_selection_cannot_be_saved(application):
    client, engine = application
    old, live = _overview(OLDER, 'Historical'), _overview(NEWER, 'Current with no saved revision')
    with Session(engine) as db:
        old_id, = _revisions(db, 'brief', old)
        db.add(ProjectBrief(project_id=1, **live))
        db.commit()
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TRIGGER reject_selection BEFORE INSERT ON active_generation_revisions "
            "BEGIN SELECT RAISE(ABORT, 'selection write failed'); END"
        )
    with pytest.raises(IntegrityError, match='selection write failed'):
        _activate(client, old_id)
    assert client.get(f'{BASE}/brief').json() == live
    history = _history(client, 'brief')
    assert history['active_id'] is None
    assert [row['id'] for row in history['results']] == [old_id]


def test_knowledge_activation_keeps_pair_and_original_evidence_separate_from_current_availability(
    application, monkeypatch,
):
    client, engine = application
    summary = {**_overview(OLDER, 'Historical choices'), 'project_state': {'publication_id': 'old',
               'pipeline': 'knowledge-v1'}}
    saved = {'project_id': 1, 'publication_id': 'old', 'generated_at': OLDER, 'summary': summary,
             'extraction_version': 'v1', 'source_manifest': {'entries': []}, 'sources': [],
             'items': [{'id': 'choice', 'statement': 'Keep metadata', 'scope': 'project',
                        'design_status': 'AGREED', 'extraction_version': 'v1', 'source_state': 'valid',
                        'evidence': [{'source_id': 'message', 'source_key': 'document', 'revision': 'old'}]}]}
    invalid = deepcopy(saved)
    invalid['summary']['project_state']['publication_id'] = 'wrong-pair'
    live = _overview(NEWER, 'Independent Overview')
    monkeypatch.setattr(routes, 'load_sources', lambda *args, **kwargs: {
        'sources': [], 'manifest': {'entries': [{'source_key': 'document', 'availability': 'unavailable'}]},
        'coverage': {},
    })
    with Session(engine) as db:
        old_id, invalid_id = _revisions(db, 'knowledge', saved, invalid)
        db.add(ProjectBrief(project_id=1, **live))
        db.commit()

    assert _activate(client, invalid_id).status_code == 409
    assert client.get(f'{BASE}/knowledge').json()['snapshot'] is None
    response = _activate(client, old_id)
    assert response.status_code == 200, response.text
    state = client.get(f'{BASE}/knowledge').json()
    assert state['snapshot'] == saved
    assert state['current_evidence_availability'] == {'choice': 'unavailable'}
    assert client.get(f'{BASE}/brief').json() == live
    with Session(engine) as db:
        assert db.get(ProjectKnowledge, 1).snapshot == saved


def test_activation_checks_project_ownership_and_same_kind_generation_lock(application):
    client, engine = application
    old = _overview(OLDER, 'First')
    with Session(engine) as db:
        own_id, = _revisions(db, 'brief', old)
        foreign_id, = _revisions(db, 'brief', old, project_id=2)
        db.commit()
    assert _activate(client, foreign_id).status_code == 404
    with routes.generation_lock.for_job(1, 'brief'):
        assert _activate(client, own_id).status_code == 409
        assert client.get(f'{BASE}/brief').json() is None
    with routes.generation_lock.for_job(2, 'brief'), routes.generation_lock.for_job(1, 'scheme'):
        assert _activate(client, own_id).status_code == 200
    with Session(engine) as db:
        assert db.get(ProjectBrief, 2) is None
        assert set(db.scalars(select(GenerationRevision.id))) == {own_id, foreign_id}
