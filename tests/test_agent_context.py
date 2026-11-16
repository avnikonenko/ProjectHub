"""Stored-context projections use real SQLite; no network or AI requests."""
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.agent_context import agent_context, agent_read, KINDS
from backend.app.db import initialize
from backend.app.models import Project, ProjectBrief, ProjectKnowledge, ProjectQA, ProjectScheme, Note, Task, CodexSession, CodexEvent


@pytest.fixture
def db(tmp_path):
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as session:
        session.add_all([Project(id=1, name='One', codex_account='PRIVATE_ACCOUNT', repository_path='/private/path'),
                         Project(id=2, name='Other')])
        session.commit()
        yield session
    engine.dispose()


def seed(db):
    db.add(ProjectBrief(project_id=1, generated_at='saved-date', sections={
        'overview': [{'text': 'Saved project purpose', 'source_ids': ['note:1']}],
        'architecture': [{'text': 'API and database', 'classification': 'reported', 'source_ids': []}]}, sources=[{'text': 'SECRET FULL SOURCE'}], coverage=[]))
    db.add(ProjectKnowledge(project_id=1, snapshot={'generated_at': 'knowledge-date', 'items': [
        {'id': 'agreed', 'statement': 'Use SQLite', 'rationale': 'Local data', 'design_status': 'AGREED',
         'category': 'DECISION', 'source_state': 'stale', 'reconciliation_state': 'unresolved',
         'reconciliation_note': 'New report disagrees', 'conflicts_with': ['other'],
         'evidence': [{'source_id': 'turn:1', 'excerpt': 'DO NOT EXPORT FULL EVIDENCE'}]},
        {'id': 'rejected', 'statement': 'Old proposal', 'design_status': 'REJECTED'},
        {'id': 'superseded', 'statement': 'Old approach', 'design_status': 'AGREED', 'superseded_by': ['agreed']},
    ], 'sources': [{'text': 'SECRET KNOWLEDGE SOURCE'}]}))
    db.add(ProjectQA(project_id=1, generated_at='qa-date', items=[{'question': 'Why?', 'answer': 'Because.', 'source_ids': ['turn:1']}], sources=[]))
    db.add(ProjectScheme(project_id=1, snapshot={'generated_at': 'scheme-date', 'architecture': {
        'explanation': 'Frontend calls API', 'nodes': [{'id': 'a', 'label': 'API', 'description': 'Serves requests', 'status': 'observed', 'source_ids': ['file:1']}],
        'edges': [{'source': 'a', 'target': 'b', 'label': 'Stores data'}]},
        'sources': [{'text': 'SECRET SCHEME SOURCE'}]}))
    db.add_all([Note(id=1, project_id=1, title='Relevant note', content='SQLite reason'),
                Note(id=2, project_id=2, title='Foreign', content='FOREIGN_CONTENT'),
                Note(id=3, project_id=1, title='Imported conversation', kind='document', content='FULL CONVERSATION'),
                Task(project_id=1, title='Next check'), Task(project_id=1, title='Finished', status='completed'),
                Task(project_id=1, title='No plan', status='not_planned'),
                CodexSession(id=1, project_id=1, thread_id='thread-id', title='Discussion', codex_account='PRIVATE_ACCOUNT')])
    db.flush()
    db.add(CodexEvent(project_id=1, session_id=1, method='item/completed', payload={'item': {'type': 'agentMessage', 'text': 'Saved review'}}))
    db.add(CodexEvent(project_id=1, session_id=1, method='error', payload={'message': 'PRIVATE_DIAGNOSTIC'}))
    db.commit()


def test_context_is_compact_read_only_and_explicit_about_freshness(db):
    seed(db)
    before = db.execute(text('SELECT total_changes()')).scalar()
    result = agent_context(db, 1)
    assert result['project']['name'] == 'One'
    assert result['overview']['saved_at'] == 'saved-date'
    assert result['knowledge']['saved_at'] == 'knowledge-date'
    assert [item['id'] for item in result['knowledge']['items']] == ['agreed']
    item = result['knowledge']['items'][0]
    assert item['source_state'] == 'stale' and item['conflicts_with'] == ['other']
    assert item['reconciliation_note'] == 'New report disagrees'
    assert item['source_ids'] == ['turn:1']
    assert [item['title'] for item in result['tasks']['items']] == ['Next check']
    assert [item['title'] for item in result['notes']['items']] == ['Relevant note']
    encoded = json.dumps(result)
    for hidden in ('PRIVATE_ACCOUNT', '/private/path', 'SECRET', 'FOREIGN_CONTENT', 'FULL CONVERSATION', 'DO NOT EXPORT'):
        assert hidden not in encoded
    assert result['overview']['freshness'] == 'unknown'
    assert db.execute(text('SELECT total_changes()')).scalar() == before


def test_all_kinds_are_scoped_and_saved_snapshots_do_not_include_sources(db):
    seed(db)
    for kind in KINDS:
        result = agent_read(db, 1, kind)
        assert result['items'], kind
        assert 'SECRET' not in json.dumps(result)
        assert 'PRIVATE' not in json.dumps(result)
        assert 'FOREIGN_CONTENT' not in json.dumps(result)
    assert agent_read(db, 1, 'scheme')['total'] == 3
    assert agent_read(db, 1, 'responses')['items'][0]['answer'] == 'Saved review'
    assert agent_read(db, 2, 'notes', record_id=1)['items'] == []
    assert agent_read(db, 2, 'responses', record_id=1)['items'] == []
    assert agent_read(db, 2, 'knowledge', record_id='agreed')['items'] == []


def test_paging_and_text_windows_preserve_full_saved_prose(db):
    content = '0123456789' * 250000
    db.add_all([Note(project_id=1, title='Huge', content=content), Note(project_id=1, title='Second', content='end')])
    db.commit()
    page = agent_read(db, 1, 'notes', limit=1)
    assert page['items'][0]['title'] == 'Second' and page['next_offset'] == 1
    last = agent_read(db, 1, 'notes', limit=1, offset=1)
    assert last['next_offset'] is None
    row = last['items'][0]
    assert row['content'] == content[:3000] and row['content_next_text_offset'] == 3000
    next_text = agent_read(db, 1, 'notes', record_id=row['id'], text_offset=3000)
    assert next_text['items'][0]['content'] == content[3000:6000]
    assert len(json.dumps(next_text)) < 5000
    end = agent_read(db, 1, 'notes', record_id=row['id'], text_offset=len(content))
    assert end['items'][0]['content'] == '' and end['items'][0]['content_next_text_offset'] is None


def test_query_is_literal_and_does_not_change_overview_or_invoke_generation(db):
    seed(db)
    result = agent_context(db, 1, 'SQLite')
    assert result['knowledge']['items'][0]['statement'] == 'Use SQLite'
    assert result['notes']['total'] == 1
    assert result['tasks']['items'] == []
    assert result['overview']['total'] == 2
    assert agent_context(db, 1, "' OR 1=1 --")['knowledge']['items'] == []


def test_empty_and_invalid_inputs(db):
    assert agent_context(db, 1)['overview']['items'] == []
    for kind in KINDS:
        assert agent_read(db, 1, kind)['items'] == []
    with pytest.raises(HTTPException) as missing:
        agent_context(db, 999)
    assert missing.value.status_code == 404
    for options in ({'kind': 'bad'}, {'kind': 'notes', 'offset': -1}, {'kind': 'notes', 'limit': 21},
                    {'kind': 'notes', 'text_offset': -1}):
        with pytest.raises(HTTPException):
            agent_read(db, 1, **options)


def test_context_prioritizes_project_purpose_over_long_change_lists(db):
    db.add(ProjectBrief(project_id=1, generated_at='date', sections={
        'changes': [{'text': f'Change {index}', 'source_ids': []} for index in range(20)],
        'architecture': [{'text': 'Architecture', 'source_ids': []}],
        'overview': [{'text': 'Purpose', 'source_ids': []}],
        'current_status': [{'text': 'Current', 'source_ids': []}],
    }, sources=[], coverage=[]))
    db.commit()
    result = agent_context(db, 1)['overview']
    assert [item['text'] for item in result['items'][:3]] == ['Purpose', 'Current', 'Architecture']
    assert result['total'] == 23 and result['next_offset'] == 10


def test_reference_arrays_are_bounded_and_limit_is_disclosed(db):
    db.add(ProjectKnowledge(project_id=1, snapshot={'items': [{
        'id': 'many', 'statement': 'Bounded', 'design_status': 'AGREED',
        'conflicts_with': [f'conflict:{i}' for i in range(30)],
        'evidence': [{'source_id': f'source:{i}', 'excerpt': 'not exported'} for i in range(30)],
    }]}))
    db.commit()
    result = agent_read(db, 1, 'knowledge')
    assert result['reference_limit'] == 10
    assert len(result['items'][0]['source_ids']) == 10
    assert len(result['items'][0]['conflicts_with']) == 10


def test_large_knowledge_and_overview_never_return_full_source_payloads(db):
    huge = 'large prose ' * 100000
    db.add(ProjectKnowledge(project_id=1, snapshot={
        'generated_at': 'date', 'items': [{'id': 'huge', 'statement': huge, 'rationale': huge,
                                         'design_status': 'AGREED', 'evidence': [{'source_id': 'source', 'excerpt': huge}]}],
        'sources': [{'text': huge}],
    }))
    db.add(ProjectBrief(project_id=1, generated_at='date', sections={
        'overview': [{'text': huge, 'source_ids': ['source']}]}, sources=[{'text': huge}], coverage=[]))
    db.commit()
    for kind in ('overview', 'knowledge'):
        result = agent_read(db, 1, kind)
        assert len(json.dumps(result)) < 12000
        item = result['items'][0]
        field = 'text' if kind == 'overview' else 'statement'
        assert item[field + '_length'] == len(huge)
        assert item[field + '_next_text_offset'] == 3000
