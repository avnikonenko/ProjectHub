"""Keep older Knowledge revisions usable after deleting a paired Overview."""
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex.router import (
    activate_saved_revision, delete_brief_revision, revision_snapshot,
)
from backend.app.models import GenerationRevision, Project, ProjectKnowledge


@pytest.fixture
def history(tmp_path):
    engine, _ = initialize(tmp_path)
    try:
        with Session(engine) as db:
            db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')])
            db.commit()
        yield engine
    finally:
        engine.dispose()


def _summary(label):
    return {
        'generated_at': 'then',
        'project_state': {'publication_id': 'legacy', 'pipeline': 'knowledge-v1'},
        'sections': {'overview': [{'text': label, 'source_ids': []}]},
        'sources': [], 'coverage': [],
    }


def _snapshot(**changes):
    return {
        'project_id': 1, 'publication_id': 'legacy', 'generated_at': 'then',
        'extraction_version': 'v1', 'items': [], 'sources': [], **changes,
    }


def _revision(db, kind, snapshot, project_id=1):
    row = GenerationRevision(project_id=project_id, kind=kind,
                             generated_at=snapshot['generated_at'], snapshot=snapshot)
    db.add(row)
    db.flush()
    return row.id


def test_deletion_preserves_each_paired_revision_and_legacy_activation(history):
    older_summary, preferred_summary = _summary('First copy'), _summary('Preferred copy')
    original = _snapshot()
    tagged = _snapshot(topic_grouping={'kept': True})
    embedded = _snapshot(summary=_summary('Already embedded'))
    foreign = _snapshot(project_id=2)
    newer = _snapshot(publication_id='newer', generated_at='now')
    with Session(history) as db:
        older_id = _revision(db, 'brief', older_summary)
        preferred_id = _revision(db, 'brief', preferred_summary)
        original_id = _revision(db, 'knowledge', original)
        tagged_id = _revision(db, 'knowledge', tagged)
        embedded_id = _revision(db, 'knowledge', embedded)
        foreign_id = _revision(db, 'knowledge', foreign, project_id=2)
        newer_id = _revision(db, 'knowledge', newer)
        db.add(ProjectKnowledge(project_id=1, snapshot=newer))
        db.commit()

    with Session(history) as db:
        delete_brief_revision(1, older_id, db, older_summary['generated_at'])
        assert db.get(GenerationRevision, original_id).snapshot == original
        assert revision_snapshot(1, original_id, db, original['generated_at'])['snapshot']['summary'] == preferred_summary
        delete_brief_revision(1, preferred_id, db, preferred_summary['generated_at'])

    with Session(history) as db:
        assert db.get(GenerationRevision, preferred_id) is None
        assert db.get(GenerationRevision, original_id).snapshot == {**original, 'summary': preferred_summary}
        assert db.get(GenerationRevision, tagged_id).snapshot == {**tagged, 'summary': preferred_summary}
        assert db.get(GenerationRevision, embedded_id).snapshot == embedded
        assert db.get(GenerationRevision, foreign_id).snapshot == foreign
        assert db.get(GenerationRevision, newer_id).snapshot == newer
        assert db.get(ProjectKnowledge, 1).snapshot == newer
        assert activate_saved_revision(1, tagged_id, db, tagged['generated_at'])['snapshot'] == {
            **tagged, 'summary': preferred_summary,
        }


def test_failed_delete_rolls_back_copy_to_historical_revision(history):
    summary, snapshot = _summary('Backing Summary'), _snapshot()
    with Session(history) as db:
        overview_id = _revision(db, 'brief', summary)
        knowledge_id = _revision(db, 'knowledge', snapshot)
        db.commit()

    staged = []

    def reject_delete(_connection, _cursor, statement, _params, _context, _executemany):
        if statement.startswith('UPDATE generation_revisions'):
            staged.append(True)
        if statement.startswith('DELETE FROM generation_revisions'):
            raise RuntimeError('Fixture delete failure')

    event.listen(history, 'before_cursor_execute', reject_delete)
    try:
        with Session(history) as db, pytest.raises(RuntimeError, match='Fixture delete failure'):
            delete_brief_revision(1, overview_id, db, summary['generated_at'])
    finally:
        event.remove(history, 'before_cursor_execute', reject_delete)

    assert staged == [True]
    with Session(history) as db:
        assert db.get(GenerationRevision, overview_id).snapshot == summary
        assert db.get(GenerationRevision, knowledge_id).snapshot == snapshot
