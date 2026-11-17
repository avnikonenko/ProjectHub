"""Review lifecycle with real SQLite and synthetic App Server notifications."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.integrations.codex.manager import CodexManager
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import Base, CodexSession, Decision, Delivery, Project


@pytest.fixture
def review_manager(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'review.sqlite3'))
    Base.metadata.create_all(engine)
    manager = CodexManager(engine, Path(tmp_path))
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Other')])
        db.flush()
        db.add_all([CodexSession(id=1, project_id=1, thread_id='one', status='running'),
                    CodexSession(id=2, project_id=2, thread_id='two', status='running')])
        db.add(Decision(id=1, project_id=1, title='Decision', status='accepted'))
        db.flush()
        db.add_all([
            Delivery(id=1, project_id=1, session_id=1, content='Review', status='sent', turn_id='turn-one'),
            Delivery(id=2, project_id=1, session_id=1, content='Queued', status='queued'),
            Delivery(id=3, project_id=2, session_id=2, content='Other', status='sent', turn_id='turn-two'),
            Delivery(id=4, project_id=1, session_id=1, decision_id=1, content='Decision', status='draft'),
        ])
        db.commit()
    yield engine, manager
    engine.dispose()


def test_terminal_error_fails_only_active_review_requests(review_manager):
    engine, manager = review_manager
    with Session(engine) as db:
        db.get(Delivery, 4).status = 'sent'
        db.commit()
    manager._event(1, 'error', {'message': 'Synthetic terminal error', 'willRetry': False})
    with Session(engine) as db:
        assert db.get(CodexSession, 1).status == 'failed'
        assert db.get(Delivery, 1).status == 'failed'
        assert db.get(Delivery, 1).error
        assert db.get(Delivery, 2).status == 'queued'
        assert db.get(Delivery, 3).status == 'sent'
        assert db.get(Delivery, 4).status == 'sent'


def test_transient_error_preserves_active_review(review_manager):
    engine, manager = review_manager
    manager._event(1, 'error', {'willRetry': True})
    with Session(engine) as db:
        assert db.get(CodexSession, 1).status == 'running'
        assert db.get(Delivery, 1).status == 'sent'
        assert db.get(Delivery, 1).error == ''


def test_late_actual_completion_reconciles_terminal_review_error(review_manager):
    engine, manager = review_manager
    manager._event(1, 'error', {'willRetry': False})
    manager._event(1, 'turn/completed', {'turn': {'id': 'unrelated-turn', 'status': 'completed'}})
    with Session(engine) as db:
        assert db.get(Delivery, 1).status == 'failed'
    manager._event(1, 'turn/completed', {'turn': {'id': 'turn-one', 'status': 'completed'}})
    with Session(engine) as db:
        assert db.get(Delivery, 1).status == 'completed'
        assert db.get(Delivery, 1).error == ''
        assert db.get(CodexSession, 1).last_error == ''


@pytest.mark.parametrize('outcome', ['acknowledged', 'completed', 'ack_failed'])
def test_error_before_start_ack_preserves_terminal_status(review_manager, outcome):
    engine, manager = review_manager

    class Client:
        connected = True

        def request(self, method, params):
            if method == 'account/read':
                return {'account': {'type': 'chatgpt'}}
            if method == 'mcpServerStatus/list':
                return {'data': [], 'nextCursor': None}
            if method == 'turn/start':
                manager._event(1, 'error', {'willRetry': False})
                if outcome == 'completed':
                    manager._event(1, 'turn/completed', {'turn': {'id': 'ack-turn', 'status': 'completed'}})
                if outcome == 'ack_failed':
                    raise CodexError('App Server disconnected')
                return {'turn': {'id': 'ack-turn'}}
            raise AssertionError(method)

    manager.clients[1] = Client()
    with Session(engine) as db:
        db.get(CodexSession, 1).status = 'idle'
        delivery = db.get(Delivery, 1)
        delivery.status, delivery.turn_id = 'queued', None
        db.commit()
    result = manager.send(1)
    assert result['turn_id'] == (None if outcome == 'ack_failed' else 'ack-turn')
    assert result['status'] == ('completed' if outcome == 'completed' else 'failed')
    assert bool(result['error']) is (outcome != 'completed')
    if outcome == 'ack_failed':
        assert 'execution error' in result['error']
        with Session(engine) as db:
            assert db.get(CodexSession, 1).last_error == result['error']


@pytest.mark.parametrize('old_status', ['completed', 'failed', 'interrupted'])
def test_old_completion_does_not_reset_newer_running_review(review_manager, old_status):
    engine, manager = review_manager
    with Session(engine) as db:
        db.get(Delivery, 4).status = 'completed'
        db.add(Delivery(id=5, project_id=1, session_id=1, content='New review',
                        status='sent', turn_id='new-turn'))
        db.commit()
    manager._event(1, 'turn/started', {'turn': {'id': 'new-turn', 'status': 'inProgress'}})
    manager._event(1, 'turn/completed', {'turn': {'id': 'turn-one', 'status': old_status}})
    with Session(engine) as db:
        assert db.get(Delivery, 1).status == old_status
        assert db.get(Delivery, 5).status == 'sent'
        assert db.get(CodexSession, 1).status == 'running'
        assert db.get(CodexSession, 1).last_error == ''
    manager._event(1, 'turn/completed', {'turn': {'id': 'new-turn', 'status': 'completed'}})
    with Session(engine) as db:
        assert db.get(Delivery, 5).status == 'completed'
        assert db.get(CodexSession, 1).status == 'idle'


def test_old_completion_does_not_reset_newer_unacknowledged_review(review_manager):
    engine, manager = review_manager
    with Session(engine) as db:
        db.get(Delivery, 4).status = 'completed'
        db.add(Delivery(id=5, project_id=1, session_id=1, content='New review', status='sending'))
        db.commit()
    manager._event(1, 'turn/started', {'turn': {'id': 'turn-one', 'status': 'inProgress'}})
    manager._event(1, 'turn/completed', {'turn': {'id': 'turn-one', 'status': 'completed'}})
    with Session(engine) as db:
        assert db.get(Delivery, 1).status == 'completed'
        assert db.get(Delivery, 5).status == 'sending'
        assert db.get(CodexSession, 1).status == 'running'


def test_external_newer_turn_is_preserved_without_local_delivery(review_manager):
    engine, manager = review_manager
    manager._event(1, 'turn/started', {'turn': {'id': 'external-new', 'status': 'inProgress'}})
    manager._event(1, 'turn/completed', {'turn': {'id': 'turn-one', 'status': 'completed'}})
    with Session(engine) as db:
        assert db.get(Delivery, 1).status == 'completed'
        assert db.get(CodexSession, 1).status == 'running'


def test_current_completion_before_ack_uses_observed_start(review_manager):
    engine, manager = review_manager
    with Session(engine) as db:
        db.get(Delivery, 1).status = 'completed'
        db.add(Delivery(id=5, project_id=1, session_id=1, content='Current', status='sending'))
        db.commit()
    manager._event(1, 'turn/started', {'turn': {'id': 'current', 'status': 'inProgress'}})
    manager._event(1, 'turn/completed', {'turn': {'id': 'current', 'status': 'completed'}})
    with Session(engine) as db:
        assert db.get(CodexSession, 1).status == 'idle'
        # send() attaches the acknowledgement's turn ID and reconciles its event.
        assert db.get(Delivery, 5).status == 'sending'


@pytest.mark.parametrize('turn_id', [None, ''])
def test_completion_without_turn_identity_does_not_reset_session(review_manager, turn_id):
    engine, manager = review_manager
    manager._event(1, 'turn/completed', {'turn': {'id': turn_id, 'status': 'completed'}})
    with Session(engine) as db:
        assert db.get(CodexSession, 1).status == 'running'
        assert db.get(Delivery, 1).status == 'sent'


@pytest.mark.parametrize('newer', [None, 'observed', 'local'])
def test_ack_replays_early_completion_without_new_start_notification(review_manager, newer):
    engine, manager = review_manager

    class Client:
        connected = True

        def request(self, method, params):
            if method == 'account/read':
                return {'account': {'type': 'chatgpt'}}
            if method == 'mcpServerStatus/list':
                return {'data': [], 'nextCursor': None}
            if method == 'turn/start':
                manager._event(1, 'turn/completed', {'turn': {'id': 'current', 'status': 'completed'}})
                if newer == 'observed':
                    manager._event(1, 'turn/started', {'turn': {'id': 'external-new', 'status': 'inProgress'}})
                if newer == 'local':
                    with Session(engine) as db:
                        db.add(Delivery(id=5, project_id=1, session_id=1, content='Newer', status='sending'))
                        db.commit()
                return {'turn': {'id': 'current'}}
            raise AssertionError(method)

    manager._event(1, 'turn/started', {'turn': {'id': 'previous', 'status': 'inProgress'}})
    manager.clients[1] = Client()
    with Session(engine) as db:
        db.get(CodexSession, 1).status = 'idle'
        db.get(Delivery, 1).status = 'queued'
        db.commit()
    result = manager.send(1)
    assert result['status'] == 'completed'
    with Session(engine) as db:
        assert db.get(CodexSession, 1).status == ('idle' if newer is None else 'running')
