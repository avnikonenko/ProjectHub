"""Project-scoped session moves use real SQLite/FTS; no Codex or user-data mutations."""
import json
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import (AuditEvent, CodexEvent, CodexSession, ContextPackage,
                                Delivery, Project, ProjectBrief, Task)
from backend.app.integrations.codex import router as routes
from backend.app.integrations.codex import session_reassignment as moving
from backend.app.integrations.codex.history_search import search_history
from backend.app.integrations.codex.manager import CodexManager
from backend.app.integrations.codex.protocol import CodexError


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(id=1, name='Source'), Project(id=2, name='Destination'),
                    Project(id=3, name='Other')]); db.flush()
        db.add(CodexSession(id=9, project_id=1, thread_id='external-thread', title='External design',
                            status='linked', codex_account='original-profile', topic='Preparation',
                            history_indexed_at='captured', history_indexed_activity='activity'))
        db.add(ProjectBrief(project_id=1, generated_at='saved', sections={'overview': ['Original']},
                            sources=[{'id': 'session:9:turn:original', 'text': 'Original quotation'}], coverage=[]))
        db.flush()
        db.execute(text('INSERT INTO codex_history_turns '
                        '(project_id,session_id,session_title,turn_id,status,messages,content) '
                        "VALUES (1,9,'External design','original','completed',:messages,'Uniquehistorytoken')"),
                   {'messages': json.dumps([{'role': 'user', 'text': 'Uniquehistorytoken'}])})
        db.execute(text("INSERT INTO codex_history_index VALUES (1,'captured',1)"))
        db.commit()
    releases = []
    manager = SimpleNamespace(lock=threading.RLock(), release_session=lambda sid: releases.append(sid))
    monkeypatch.setattr(routes, 'manager_for', lambda *args, **kwargs: manager)
    app = FastAPI(); app.state.engine = engine; app.include_router(routes.router)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, engine=engine, manager=manager, releases=releases)
    engine.dispose()


def move(api, *, source=1, target=2):
    return api.client.post(f'/api/projects/{source}/codex/sessions/9/move', json={'target_project_id': target})


def test_move_preserves_identity_history_and_reports_with_project_scoped_audit(api):
    with Session(api.engine) as db:
        before = dict(db.execute(text('SELECT * FROM codex_history_turns')).mappings().one())
    response = move(api)
    assert response.status_code == 200, response.text
    row = response.json()
    assert {key: row[key] for key in ('id', 'project_id', 'thread_id', 'title', 'codex_account', 'topic', 'is_linked')} == {
        'id': 9, 'project_id': 2, 'thread_id': 'external-thread', 'title': 'External design',
        'codex_account': 'original-profile', 'topic': 'Preparation', 'is_linked': True}
    assert row['history_indexed_at'] == 'captured'
    assert api.releases == [9]
    with Session(api.engine) as db:
        after = dict(db.execute(text('SELECT * FROM codex_history_turns')).mappings().one())
        assert after == {**before, 'project_id': 2}
        assert not search_history(db, 1, 'Uniquehistorytoken')['results']
        assert search_history(db, 2, 'Uniquehistorytoken')['results'][0]['session_id'] == 9
        counts = dict(db.execute(text('SELECT project_id,indexed_turns FROM codex_history_index')).all())
        assert counts == {1: 0, 2: 1}
        report = db.get(ProjectBrief, 1)
        assert report.sections == {'overview': ['Original']}
        assert report.sources == [{'id': 'session:9:turn:original', 'text': 'Original quotation'}]
        audits = list(db.scalars(select(AuditEvent).order_by(AuditEvent.id)))
        assert [(audit.project_id, audit.action) for audit in audits] == [(1, 'session_moved_out'), (2, 'session_moved_in')]
        assert all(audit.before['project_id'] == 1 and audit.after['project_id'] == 2 for audit in audits)
    assert api.client.get('/api/projects/1/codex/sessions').json() == []
    assert api.client.get('/api/projects/2/codex/sessions').json()[0]['id'] == 9


def test_unlink_then_link_another_project_uses_safe_move_and_linked_foreign_requires_explicit_move(api):
    route = '/api/projects/2/codex/sessions'
    assert api.client.post(route, json={'thread_id': 'external-thread'}).status_code == 409
    assert api.releases == []
    response = api.client.patch('/api/projects/1/codex/sessions/9/link', json={'linked': False})
    assert response.status_code == 200
    assert api.client.get('/api/projects/1/codex/sessions').json() == []
    assert api.client.get('/api/projects/1/codex/sessions?include_unlinked=true').json()[0]['id'] == 9
    response = api.client.post(route, json={'thread_id': 'external-thread'})
    assert response.status_code == 201, response.text
    assert response.json()['project_id'] == 2 and response.json()['id'] == 9


@pytest.mark.parametrize('source,target,status', [(3, 2, 404), (1, 999, 404), (1, 1, 422)])
def test_move_enforces_source_and_destination_ownership_before_releasing(api, source, target, status):
    assert move(api, source=source, target=target).status_code == status
    assert api.releases == []
    with Session(api.engine) as db:
        assert db.get(CodexSession, 9).project_id == 1


@pytest.mark.parametrize('model,values', [
    (Task, {'title': 'Retained task'}),
    (ContextPackage, {'content': 'Retained context', 'version': 'one'}),
    (Delivery, {'content': 'Retained response'}),
    (CodexEvent, {'method': 'item/completed', 'payload': {'text': 'Retained response'}}),
])
def test_project_owned_records_are_not_silently_moved(api, model, values):
    with Session(api.engine) as db:
        db.add(model(project_id=1, session_id=9, **values)); db.commit()
    response = move(api)
    assert response.status_code == 409 and 'project-specific' in response.json()['detail']
    assert api.releases == []
    with Session(api.engine) as db:
        assert db.get(CodexSession, 9).project_id == 1
        assert db.scalar(select(model.project_id)) == 1


def test_running_session_is_never_closed_or_moved(api):
    with Session(api.engine) as db:
        db.get(CodexSession, 9).status = 'running'; db.commit()
    assert move(api).status_code == 409
    assert api.releases == []


def test_resumed_project_discussion_cannot_move_even_before_its_first_request(api):
    with Session(api.engine) as db:
        db.get(CodexSession, 9).status = 'idle'; db.commit()
    response = move(api)
    assert response.status_code == 409 and 'read-only session links' in response.json()['detail']
    assert api.releases == []


def test_stale_resume_and_interrupt_cannot_operate_on_moved_session(api):
    with Session(api.engine) as db:
        stale_session, stale_project = db.get(CodexSession, 9), db.get(Project, 1)
        db.expunge_all()
    assert move(api).status_code == 200
    manager = CodexManager.__new__(CodexManager)
    manager.engine, manager.lock, manager.account_key = api.engine, threading.RLock(), 'original-profile'
    manager.clients = {9: SimpleNamespace(request=lambda *args: pytest.fail('A stale operation reached Codex'))}
    manager._discussion_client = lambda *args: pytest.fail('A stale resume opened a Codex transport')
    with pytest.raises(CodexError, match='association changed'):
        manager.resume(stale_session, stale_project)
    with pytest.raises(CodexError, match='association changed'):
        manager.interrupt(stale_session)


def test_another_session_operation_blocks_explicit_move(api):
    with api.manager.lock:
        assert move(api).status_code == 409
    assert api.releases == []


@pytest.mark.parametrize('lock', [moving.generation_lock, moving._index_lock])
def test_active_generation_or_history_index_blocks_move(api, lock):
    with lock:
        assert move(api).status_code == 409
    assert api.releases == []


def test_release_callbacks_are_rechecked_before_reassignment(api):
    def close(sid):
        with Session(api.engine) as db:
            db.add(CodexEvent(project_id=1, session_id=sid, method='turn/started', payload={}))
            db.commit()
    api.manager.release_session = close
    response = move(api)
    assert response.status_code == 409
    with Session(api.engine) as db:
        assert db.get(CodexSession, 9).project_id == 1
        assert db.scalar(select(CodexEvent.project_id)) == 1
        assert db.execute(text('SELECT project_id FROM codex_history_turns')).scalar_one() == 1


def test_late_failure_rolls_back_session_index_and_audits(api, monkeypatch):
    def fail(*args):
        raise RuntimeError('Synthetic index accounting failure')
    with monkeypatch.context() as patch:
        patch.setattr(moving, 'update_active_count', fail)
        assert move(api).status_code == 500
    with Session(api.engine) as db:
        assert db.get(CodexSession, 9).project_id == 1
        assert db.execute(text('SELECT project_id FROM codex_history_turns')).scalar_one() == 1
        assert list(db.scalars(select(AuditEvent))) == []
        assert dict(db.execute(text('SELECT project_id,indexed_turns FROM codex_history_index')).all()) == {1: 1}
    assert move(api).status_code == 200
