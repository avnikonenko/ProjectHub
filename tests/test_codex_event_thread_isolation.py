"""Discussion callbacks cannot persist a different thread's notifications."""
from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import CodexEvent, CodexSession, Delivery, Project
from backend.app.integrations.codex.manager import CodexManager


@pytest.fixture
def state(tmp_path):
    engine, data = initialize(tmp_path / 'data')
    manager = CodexManager(engine, data)
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Other')]); db.flush()
        db.add_all([CodexSession(id=1, project_id=1, thread_id='thread-one', status='running'),
                    CodexSession(id=2, project_id=2, thread_id='thread-two', status='running')]); db.flush()
        db.add_all([Delivery(id=1, project_id=1, session_id=1, content='Review',
                             status='sent', turn_id='turn-one'),
                    Delivery(id=2, project_id=2, session_id=2, content='Other review',
                             status='sent', turn_id='turn-other')]); db.commit()
    yield engine, manager
    manager.close()
    engine.dispose()


def notification(method):
    if method == 'item/completed':
        return {'turnId': 'turn-one', 'item': {'type': 'agentMessage', 'text': 'Synthetic saved answer.'}}
    if method == 'error':
        return {'message': 'Synthetic execution error', 'willRetry': False}
    if method == 'projecthub/approvalDenied':
        return {'method': 'item/tool/requestApproval'}
    return {'turn': {'id': 'turn-one', 'status': 'completed' if method == 'turn/completed' else 'inProgress'}}


METHODS = ('item/completed', 'turn/started', 'turn/completed', 'error', 'projecthub/approvalDenied')


@pytest.mark.parametrize('method', METHODS)
@pytest.mark.parametrize('thread_id', ['thread-two', 'unlinked-thread', None, '', ['thread-one']])
def test_explicit_foreign_or_invalid_thread_never_changes_target_project(state, method, thread_id):
    engine, manager = state
    params = {**notification(method), 'threadId': thread_id}
    before = deepcopy(params)
    manager._event(1, method, params)
    assert params == before
    with Session(engine) as db:
        assert db.scalars(select(CodexEvent)).all() == []
        assert [db.get(CodexSession, i).status for i in (1, 2)] == ['running', 'running']
        assert [db.get(Delivery, i).status for i in (1, 2)] == ['sent', 'sent']


@pytest.mark.parametrize('method', METHODS)
@pytest.mark.parametrize('legacy', [False, True])
def test_matching_thread_and_legacy_notifications_keep_existing_behavior(state, method, legacy):
    engine, manager = state
    params = notification(method)
    if not legacy:
        params['threadId'] = 'thread-one'
    manager._event(1, method, params)
    with Session(engine) as db:
        event = db.scalars(select(CodexEvent)).one()
        assert (event.project_id, event.session_id, event.method) == (1, 1, method)
        assert db.get(CodexSession, 2).status == 'running'
        assert db.get(Delivery, 2).status == 'sent'
        if method == 'turn/completed':
            assert db.get(CodexSession, 1).status == 'idle'
            assert db.get(Delivery, 1).status == 'completed'
        elif method == 'error':
            assert db.get(CodexSession, 1).status == 'failed'
            assert db.get(Delivery, 1).status == 'failed'
        elif method == 'item/completed':
            assert event.payload['item']['text'] == 'Synthetic saved answer.'
