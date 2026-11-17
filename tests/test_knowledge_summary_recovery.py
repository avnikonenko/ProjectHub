"""Older detailed summaries remain available without overwriting the general view."""
from copy import deepcopy

from sqlalchemy.orm import Session

from backend.app.models import ProjectBrief, ProjectKnowledge, GenerationRevision
from backend.app.integrations.codex.knowledge import read_knowledge
from backend.app.integrations.codex.brief import read_brief
from backend.tests.integrations.test_scheme_evidence import setup


def summary(publication='knowledge-one', generated='then'):
    return {'generated_at': generated, 'sections': {'overview': []}, 'sources': [],
        'coverage': [], 'generation': None, 'project_map': None,
        'project_state': {'publication_id': publication, 'pipeline': 'knowledge-v1'}}


def test_legacy_summary_survives_general_overview_replacement_without_mutating_storage(tmp_path):
    engine = setup(tmp_path)
    try:
        original = {'project_id': 1, 'publication_id': 'knowledge-one', 'generated_at': 'then',
                    'items': [], 'sources': []}
        detailed = summary()
        general = {**summary('unrelated', 'now'), 'project_state': {'pipeline': 'general'}}
        with Session(engine) as db:
            db.add(ProjectKnowledge(project_id=1, snapshot=deepcopy(original)))
            db.add(ProjectBrief(project_id=1, **general))
            db.add(GenerationRevision(project_id=1, kind='brief', generated_at='then', snapshot=detailed))
            db.commit()
        with Session(engine) as db:
            recovered = read_knowledge(db, 1)
            assert recovered['summary'] == detailed
            assert read_brief(db, 1) == general
            assert db.get(ProjectKnowledge, 1).snapshot == original
    finally:
        engine.dispose()


def test_summary_recovery_rejects_foreign_publications_and_timestamp_mismatch(tmp_path):
    engine = setup(tmp_path)
    try:
        original = {'project_id': 1, 'publication_id': 'knowledge-one', 'generated_at': 'then',
                    'items': [], 'sources': []}
        with Session(engine) as db:
            db.add(ProjectKnowledge(project_id=1, snapshot=original))
            db.add_all([
                GenerationRevision(project_id=2, kind='brief', generated_at='then', snapshot=summary()),
                GenerationRevision(project_id=1, kind='brief', generated_at='then', snapshot=summary('other')),
                GenerationRevision(project_id=1, kind='brief', generated_at='wrong', snapshot=summary(generated='wrong')),
            ])
            db.commit()
        with Session(engine) as db:
            assert read_knowledge(db, 1) == original
    finally:
        engine.dispose()


def test_legacy_current_detailed_brief_can_supply_summary_until_archived(tmp_path):
    engine = setup(tmp_path)
    try:
        detailed = summary()
        with Session(engine) as db:
            db.add(ProjectKnowledge(project_id=1, snapshot={'project_id': 1,
                'publication_id': 'knowledge-one', 'generated_at': 'then', 'items': [], 'sources': []}))
            db.add(ProjectBrief(project_id=1, **detailed))
            db.commit()
        with Session(engine) as db:
            assert read_knowledge(db, 1)['summary'] == detailed
    finally:
        engine.dispose()
