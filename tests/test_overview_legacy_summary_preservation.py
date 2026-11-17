"""General Overview refresh cannot destroy the only saved legacy Summary."""
from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.integrations.codex import brief, knowledge, progress
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import GenerationRevision, ProjectBrief, ProjectKnowledge
from backend.tests.integrations.test_brief import FakeCodex, project_db


def seed_legacy(engine, *, durable=False):
    detailed = {'generated_at': 'legacy-time',
        'sections': {'overview': [{'text': 'Detailed legacy perspective.',
            'classification': 'reported', 'source_ids': []}]},
        'sources': [], 'coverage': [], 'project_map': {},
        'generation': {'account': 'fixture', 'model': 'fixture', 'effort': 'medium'},
        'project_state': {'pipeline': 'knowledge-v1', 'publication_id': 'legacy-publication'}}
    snapshot = {'project_id': 1, 'publication_id': 'legacy-publication',
        'generated_at': detailed['generated_at'], 'items': [], 'sources': [],
        'topic_groups': [{'id': 'group', 'label': 'Kept tags'}], 'topic_grouping': {'version': 'kept'}}
    if durable:
        snapshot['summary'] = deepcopy(detailed)
    with Session(engine) as db:
        db.add(ProjectBrief(project_id=1, **deepcopy(detailed)))
        db.add(ProjectKnowledge(project_id=1, snapshot=deepcopy(snapshot)))
        db.commit()
    return detailed, snapshot


def state(engine):
    with Session(engine) as db:
        return (deepcopy(db.get(ProjectKnowledge, 1).snapshot), brief.read_brief(db, 1),
            [(row.kind, deepcopy(row.snapshot)) for row in db.scalars(select(GenerationRevision)
                .where(GenerationRevision.project_id == 1).order_by(GenerationRevision.id))])


def test_general_refresh_preserves_current_only_summary_in_one_writer_transaction(project_db, monkeypatch):
    detailed, original = seed_legacy(project_db)
    preserve = knowledge.preserve_legacy_knowledge_summary
    transactions = []

    def checked_preserve(db, project_id):
        transactions.append(db.connection().connection.driver_connection.in_transaction)
        return preserve(db, project_id)

    monkeypatch.setattr(knowledge, 'preserve_legacy_knowledge_summary', checked_preserve)
    refreshed = brief.refresh_brief(project_db, 1, FakeCodex())
    stored, general, revisions = state(project_db)
    assert stored == {**original, 'summary': detailed}
    assert general == refreshed and general != detailed
    assert transactions == [True]
    assert len(revisions) == 1 and revisions[0][0] == 'brief'
    assert stored['publication_id'] == original['publication_id']


def test_already_durable_summary_and_tags_are_not_rewritten_by_general_refresh(project_db):
    _, original = seed_legacy(project_db, durable=True)
    brief.refresh_brief(project_db, 1, FakeCodex())
    assert state(project_db)[0] == original


def test_failed_generation_never_prematurely_preserves_or_changes_legacy_summary(project_db):
    seed_legacy(project_db)
    before = state(project_db)
    manager = FakeCodex()
    manager.foreign_citation = True
    with pytest.raises(CodexError):
        brief.refresh_brief(project_db, 1, manager)
    assert state(project_db) == before


@pytest.mark.parametrize('cancelled', [False, True])
def test_publication_failure_or_cancellation_rolls_back_summary_preservation(project_db, monkeypatch, cancelled):
    seed_legacy(project_db)
    before = state(project_db)
    original_save = brief.save_revision
    staged = []

    def reject_after_preservation(db, project_id, kind, snapshot):
        staged.append('summary' in db.get(ProjectKnowledge, 1).snapshot)
        if cancelled:
            original_save(db, project_id, kind, snapshot)
            progress.current_cancel_event().set()
        else:
            raise CodexError('Fixture publication failure')

    monkeypatch.setattr(brief, 'save_revision', reject_after_preservation)
    with pytest.raises(CodexError):
        brief.refresh_brief(project_db, 1, FakeCodex())
    assert staged == [True]
    assert state(project_db) == before
