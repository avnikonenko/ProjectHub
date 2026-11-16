"""Regression coverage for saved-context matching and Unicode continuation."""

import pytest
from sqlalchemy.orm import Session

from backend.app.agent_context import agent_context, agent_read
from backend.app.db import initialize
from backend.app.models import Note, Project


@pytest.fixture
def db(tmp_path):
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as session:
        session.add_all([Project(id=1, name='One'), Project(id=2, name='Other')])
        session.commit()
        yield session
    engine.dispose()


@pytest.mark.parametrize('query', ['énergie', 'žluťoučký', 'STRASSE'])
def test_context_query_matches_unicode_case_insensitively(db, query):
    db.add(Note(project_id=1, title='ÉNERGIE', content='ŽLUŤOUČKÝ Straße'))
    db.add(Note(project_id=2, title='ÉNERGIE', content='ŽLUŤOUČKÝ Straße'))
    db.commit()

    result = agent_context(db, 1, query)

    assert result['notes']['total'] == 1
    assert len(result['notes']['items']) == 1


def test_note_continuation_uses_characters_and_keeps_record_identity(db):
    content = '🧪Ž漢字' * 1601
    note = Note(project_id=1, title='Unicode', content=content)
    foreign = Note(project_id=2, title='Other', content='Foreign text')
    db.add_all([note, foreign])
    db.commit()

    pieces = []
    offset = 0
    while offset is not None:
        page = agent_read(db, 1, 'notes', record_id=str(note.id), text_offset=offset)
        assert page['total'] == 1
        assert page['next_offset'] is None
        item = page['items'][0]
        assert item['id'] == note.id
        assert item['content_length'] == len(content)
        pieces.append(item['content'])
        offset = item['content_next_text_offset']

    assert ''.join(pieces) == content
    assert agent_read(db, 1, 'notes', record_id=str(foreign.id))['items'] == []
