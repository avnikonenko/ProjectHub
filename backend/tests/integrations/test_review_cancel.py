"""Specific-review cancellation with real SQLite and a mocked interrupt transport."""
from threading import RLock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.integrations.codex.protocol import CodexError
from backend.app.integrations.codex.review_cancel import cancel_review
from backend.app.models import AuditEvent, Base, CodexEvent, CodexSession, Decision, Delivery, Project


@pytest.fixture
def review(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'reviews.sqlite3'))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Other')])
        db.flush()
        db.add_all([CodexSession(id=1, project_id=1, thread_id='thread-one', status='running'),
                    CodexSession(id=2, project_id=2, thread_id='thread-two', status='running')])
        db.flush()
        db.add_all([Delivery(id=1, project_id=1, session_id=1, content='Review', status='queued'),
                    Delivery(id=2, project_id=2, session_id=2, content='Other review', status='sent', turn_id='other')])
        db.commit()
    calls = []
    manager = SimpleNamespace(lock=RLock(), interrupt=lambda session: (
        calls.append((session.id, session.thread_id)) or {'status': 'interrupt_requested'}))
    yield engine, manager, calls
    engine.dispose()


def test_queued_review_cancel_is_persistent_audited_and_idempotent(review):
    engine, manager, calls = review
    with Session(engine) as db:
        result = cancel_review(db, 1, 1, manager)
        assert result['status'] == 'cancelled' and result['error'] == ''
        assert not result['cancel_requested']
        assert cancel_review(db, 1, 1, manager)['status'] == 'cancelled'
        events = list(db.scalars(select(AuditEvent)))
        assert [event.action for event in events] == ['review_cancelled']
        assert events[0].before['status'] == 'queued'
        assert events[0].after['status'] == 'cancelled'
    assert calls == []


def test_foreign_project_and_decision_deliveries_cannot_be_cancelled(review):
    engine, manager, calls = review
    with Session(engine) as db:
        with pytest.raises(HTTPException) as error:
            cancel_review(db, 1, 2, manager)
        assert error.value.status_code == 404
        db.add(Decision(id=1, project_id=1, title='Accepted choice', status='accepted'))
        db.flush()
        db.get(Delivery, 1).decision_id = 1
        db.commit()
        with pytest.raises(CodexError, match='Only review'):
            cancel_review(db, 1, 1, manager)
        assert db.get(Delivery, 1).status == 'queued'
        assert db.get(Delivery, 2).status == 'sent'
    assert calls == []


def test_active_review_interrupt_targets_exact_request_and_preserves_response(review):
    engine, manager, calls = review
    with Session(engine) as db:
        row = db.get(Delivery, 1)
        row.status, row.turn_id = 'sent', 'turn-one'
        db.add_all([
            CodexEvent(project_id=1, session_id=1, method='turn/started', payload={'turn': {'id': 'turn-one'}}),
            CodexEvent(project_id=1, session_id=1, method='item/completed',
                       payload={'item': {'type': 'agentMessage', 'text': 'Existing saved response'}}),
        ])
        db.commit()
        result = cancel_review(db, 1, 1, manager)
        assert result['status'] == 'sent' and result['cancel_requested'] is True
        assert calls == [(1, 'thread-one')]
        assert db.get(Delivery, 1).error == ''
        assert db.scalar(select(CodexEvent).where(CodexEvent.method == 'item/completed')).payload['item']['text'] == 'Existing saved response'
        assert db.scalar(select(AuditEvent)).action == 'review_cancel_requested'


@pytest.mark.parametrize('newer_delivery', [True, False])
def test_newer_or_external_turn_is_never_interrupted(review, newer_delivery):
    engine, manager, calls = review
    with Session(engine) as db:
        row = db.get(Delivery, 1)
        row.status, row.turn_id = 'sent', 'old-turn'
        if newer_delivery:
            db.add(Delivery(project_id=1, session_id=1, content='Newer review', status='sent', turn_id='new-turn'))
        else:
            db.add(CodexEvent(project_id=1, session_id=1, method='turn/started', payload={'turn': {'id': 'external-turn'}}))
        db.commit()
        with pytest.raises(CodexError, match='different'):
            cancel_review(db, 1, 1, manager)
        assert db.get(Delivery, 1).status == 'sent'
    assert calls == []


@pytest.mark.parametrize('status', ['sending', 'unverified'])
def test_unconfirmed_deliveries_are_not_claimed_cancelled(review, status):
    engine, manager, calls = review
    with Session(engine) as db:
        db.get(Delivery, 1).status = status
        db.commit()
        with pytest.raises(CodexError, match='not confirmed'):
            cancel_review(db, 1, 1, manager)
        assert db.get(Delivery, 1).status == status
    assert calls == []


def test_completion_race_keeps_actual_result(review):
    engine, manager, calls = review
    def interrupt(session):
        calls.append(session.id)
        with Session(engine) as db:
            db.get(Delivery, 1).status = 'completed'
            db.commit()
        return {'status': 'interrupt_requested'}
    manager.interrupt = interrupt
    with Session(engine) as db:
        row = db.get(Delivery, 1)
        row.status, row.turn_id = 'sent', 'turn-one'
        db.commit()
        result = cancel_review(db, 1, 1, manager)
        assert result['status'] == 'completed' and not result['cancel_requested']
        assert db.get(Delivery, 1).error == ''
    assert calls == [1]


@pytest.mark.parametrize('payload', [[], {'turn': None}, {'turn': {'id': 'turn-one'}}])
def test_completed_or_malformed_event_never_interrupts(review, payload):
    engine, manager, calls = review
    with Session(engine) as db:
        row = db.get(Delivery, 1)
        row.status, row.turn_id = 'sent', 'turn-one'
        db.add(CodexEvent(project_id=1, session_id=1, method='turn/completed', payload=payload))
        db.commit()
        with pytest.raises(CodexError, match='different or completed'):
            cancel_review(db, 1, 1, manager)
    assert calls == []


def test_foreign_related_session_cannot_be_cancelled(review):
    engine, manager, calls = review
    with Session(engine) as db:
        db.get(Delivery, 1).session_id = 2
        db.commit()
        with pytest.raises(HTTPException) as error:
            cancel_review(db, 1, 1, manager)
        assert error.value.status_code == 404
        assert db.get(Delivery, 1).status == 'queued'
    assert calls == []
