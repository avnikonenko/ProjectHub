"""Malformed saved identities and NUL text cannot bypass bounded agent reads."""
import json

from fastapi import HTTPException
import pytest
from sqlalchemy.orm import Session

from backend.app.agent_context import agent_context, agent_read
from backend.app.db import initialize
from backend.app.models import Project, ProjectKnowledge, ProjectScheme, Note, Task, ProjectBrief, ProjectQA
from backend.app.services import validate_repository
from backend.app.schemas import NoteCreate


@pytest.fixture
def db(tmp_path):
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as session:
        session.add_all([Project(id=1, name='One'), Project(id=2, name='Other')])
        session.commit()
        yield session
    engine.dispose()


@pytest.mark.parametrize('snapshot_owner', [None, 1, 2])
def test_knowledge_checks_explicit_snapshot_and_item_owners_without_rejecting_legacy(db, snapshot_owner):
    snapshot = {'items': [
        {'id': 'legacy', 'statement': 'Legacy owned text', 'design_status': 'AGREED'},
        {'id': 'own', 'project_id': 1, 'statement': 'Own text', 'design_status': 'AGREED'},
        {'id': 'foreign', 'project_id': 2, 'statement': 'FOREIGN_SECRET', 'design_status': 'AGREED'},
    ]}
    if snapshot_owner is not None:
        snapshot['project_id'] = snapshot_owner
    db.add(ProjectKnowledge(project_id=1, snapshot=snapshot)); db.commit()
    for result in (agent_read(db, 1, 'knowledge'), agent_context(db, 1)['knowledge']):
        assert 'FOREIGN_SECRET' not in json.dumps(result)
        assert {item['id'] for item in result['items']} == (set() if snapshot_owner == 2 else {'own', 'legacy'})
        assert result['total'] == (0 if snapshot_owner == 2 else 2)
    assert agent_read(db, 1, 'knowledge', record_id='foreign')['items'] == []


@pytest.mark.parametrize('snapshot_owner', [None, 1, 2])
def test_scheme_checks_explicit_snapshot_and_component_owners(db, snapshot_owner):
    snapshot = {'architecture': {'nodes': [
        {'id': 'own', 'project_id': 1, 'description': 'Owned component'},
        {'id': 'foreign', 'project_id': 2, 'description': 'FOREIGN_COMPONENT'},
    ], 'edges': [], 'explanation': 'Legacy graph explanation'}}
    if snapshot_owner is not None:
        snapshot['project_id'] = snapshot_owner
    db.add(ProjectScheme(project_id=1, snapshot=snapshot)); db.commit()
    result = agent_read(db, 1, 'scheme')
    assert 'FOREIGN_COMPONENT' not in json.dumps(result)
    assert len(result['items']) == (0 if snapshot_owner == 2 else 2)


def test_unicode_and_nul_windows_reconstruct_exact_note_including_existing_control_characters(db):
    body = 'α' * 2998 + '\x00' + '😀\x01remaining\x00tail'
    payload = NoteCreate(title='Saved text separator', content=body)
    note = Note(project_id=1, **payload.model_dump())
    db.add(note); db.commit()
    offset, pieces = 0, []
    while True:
        result = agent_read(db, 1, 'notes', record_id=note.id, text_offset=offset)
        item, = result['items']
        assert item['content_length'] == len(body)
        assert len(item['content']) <= 3000
        pieces.append(item['content'])
        offset = item['content_next_text_offset']
        if offset is None:
            break
    assert ''.join(pieces) == body
    context = agent_context(db, 1)['notes']['items'][0]
    assert context['content_length'] == len(body)
    assert context['content_next_text_offset'] == 600
    assert db.get(Note, note.id).content == body


def test_nul_prose_is_preserved_for_other_saved_information_kinds(db):
    body = 'Before\x00after 😀'
    db.add_all([
        Task(project_id=1, title='Task', description=body),
        ProjectBrief(project_id=1, generated_at='now', sections={'overview': [{'text': body}]}, sources=[], coverage=[]),
        ProjectQA(project_id=1, generated_at='now', items=[{'question': 'Why?', 'answer': body}], sources=[]),
        ProjectKnowledge(project_id=1, snapshot={'items': [{'id': 'k', 'statement': body}]}),
    ]); db.commit()
    for kind, field in [('tasks', 'description'), ('overview', 'text'), ('qa', 'answer'), ('knowledge', 'statement')]:
        item, = agent_read(db, 1, kind)['items']
        assert item[field] == body
        assert item[field + '_length'] == len(body)
        assert item[field + '_next_text_offset'] is None


def test_unresolvable_repository_home_is_a_validation_error(monkeypatch):
    def fail_if_called(*args, **kwargs):
        pytest.fail('Git must not run after an unresolvable user home.')
    monkeypatch.setattr('backend.app.services.subprocess.run', fail_if_called)
    with pytest.raises(HTTPException) as error:
        validate_repository('~__projecthub_nonexistent_user_329867__/repo')
    assert error.value.status_code == 422
