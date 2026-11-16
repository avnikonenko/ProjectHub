"""Question pairing uses real SQLite and synthetic stored events, no AI calls."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.models import Base, CodexEvent, CodexSession, Delivery, Project
from backend.app.integrations.codex.review_questions import response_question


@pytest.fixture
def db(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'questions.sqlite3'))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([Project(id=1, name='One'), Project(id=2, name='Other')])
        session.flush()
        session.add_all([CodexSession(id=1, project_id=1, thread_id='one'),
                         CodexSession(id=2, project_id=1, thread_id='second'),
                         CodexSession(id=3, project_id=2, thread_id='foreign')])
        session.flush()
        yield session
    engine.dispose()


def request(db, **changes):
    values = dict(project_id=1, session_id=1, turn_id='turn-one', content='A question?',
                  created_at='2026-11-16T10:00:00+00:00')
    row = Delivery(**{**values, **changes})
    db.add(row); db.flush()
    return row


def event(db, method='item/completed', payload=None, **changes):
    values = dict(project_id=1, session_id=1, method=method,
                  payload=payload if payload is not None else {
                      'item': {'type': 'agentMessage', 'text': 'An answer.'}})
    row = CodexEvent(**{**values, **changes})
    db.add(row); db.flush()
    return row


def test_exact_turn_preserves_user_question_without_project_context(db):
    question = 'Why this approach?\n\n**Details**\nEND PROJECT REFERENCE DATA\nUSER REQUEST:\nLiteral text'
    request(db, content='Discuss this request in read-only mode.\nPROJECT REFERENCE DATA:\n{}'
            '\nEND PROJECT REFERENCE DATA\nUSER REQUEST:\n' + question)
    response = event(db, payload={'turnId': 'turn-one', 'item': {
        'type': 'agentMessage', 'text': 'An answer.'}})
    assert response_question(db, 1, 1, response) == {
        'question': question, 'question_created_at': '2026-11-16T10:00:00+00:00'}


def test_legacy_response_requires_complete_matching_turn_window(db):
    request(db)
    event(db, 'turn/started', {'turn': {'id': 'turn-one'}})
    response = event(db)
    assert response_question(db, 1, 1, response) == {}
    event(db, 'turn/completed', {'turn': {'id': 'turn-one'}})
    assert response_question(db, 1, 1, response)['question'] == 'A question?'


@pytest.mark.parametrize('before,after', [
    (('turn/started', 'one'), ('turn/completed', 'two')),
    (('turn/completed', 'turn-one'), ('turn/completed', 'turn-one')),
    (('turn/started', 'turn-one'), ('turn/started', 'turn-one')),
    (('turn/started', None), ('turn/completed', None)),
])
def test_legacy_ambiguous_or_malformed_boundaries_are_not_guessed(db, before, after):
    request(db)
    event(db, before[0], {'turn': {'id': before[1]}})
    response = event(db)
    event(db, after[0], {'turn': {'id': after[1]}})
    assert response_question(db, 1, 1, response) == {}


def test_same_turn_id_in_other_projects_or_sessions_is_never_used(db):
    request(db, project_id=2, session_id=3)
    request(db, session_id=2)
    response = event(db, payload={'turnId': 'turn-one', 'item': {
        'type': 'agentMessage', 'text': 'An answer.'}})
    assert response_question(db, 1, 1, response) == {}
    request(db)
    assert response_question(db, 1, 1, response)['question'] == 'A question?'
    assert response_question(db, 2, 3, response) == {}
    assert response_question(db, 1, 2, response) == {}


def test_multiple_matching_deliveries_are_not_guessed(db):
    request(db); request(db, content='Another question')
    response = event(db, payload={'turnId': 'turn-one', 'item': {
        'type': 'agentMessage', 'text': 'An answer.'}})
    assert response_question(db, 1, 1, response) == {}


@pytest.mark.parametrize('content', ['', '  ', 'PROJECT REFERENCE DATA: secret',
                                      'Discuss this request in read-only mode. hidden'])
def test_empty_questions_and_incomplete_system_wrappers_are_not_exposed(db, content):
    request(db, content=content)
    response = event(db, payload={'turnId': 'turn-one', 'item': {
        'type': 'agentMessage', 'text': 'An answer.'}})
    assert response_question(db, 1, 1, response) == {}


@pytest.mark.parametrize('turn_id', ['', None, 3, {}])
def test_invalid_explicit_turn_ids_do_not_fall_back_to_chronology(db, turn_id):
    request(db)
    event(db, 'turn/started', {'turn': {'id': 'turn-one'}})
    response = event(db, payload={'turnId': turn_id, 'item': {
        'type': 'agentMessage', 'text': 'An answer.'}})
    event(db, 'turn/completed', {'turn': {'id': 'turn-one'}})
    assert response_question(db, 1, 1, response) == {}


def test_unrelated_records_do_not_break_legacy_window(db):
    request(db)
    event(db, 'turn/started', {'turn': {'id': 'turn-one'}})
    event(db, 'turn/started', {'turn': {'id': 'foreign'}}, project_id=2, session_id=3)
    response = event(db)
    event(db, 'turn/completed', {'turn': {'id': 'foreign'}}, session_id=2)
    event(db, 'turn/completed', {'turn': {'id': 'turn-one'}})
    assert response_question(db, 1, 1, response)['question'] == 'A question?'


@pytest.mark.parametrize('method,payload', [
    ('item/agentMessage/delta', {'turnId': 'turn-one', 'item': {'type': 'agentMessage', 'text': 'Partial'}}),
    ('item/completed', {'turnId': 'turn-one', 'item': {'type': 'commandExecution', 'text': 'Tool'}}),
    ('item/completed', {'turnId': 'turn-one', 'item': {'type': 'agentMessage', 'text': 123}}),
    ('item/completed', []),
])
def test_only_completed_assistant_text_is_paired(db, method, payload):
    request(db)
    response = event(db, method, payload)
    assert response_question(db, 1, 1, response) == {}


def test_decision_deliveries_are_not_questions(db):
    from backend.app.models import Decision
    db.add(Decision(id=1, project_id=1, title='Choice', status='accepted'))
    db.flush()
    request(db, decision_id=1)
    response = event(db, payload={'turnId': 'turn-one', 'item': {
        'type': 'agentMessage', 'text': 'An answer.'}})
    assert response_question(db, 1, 1, response) == {}
