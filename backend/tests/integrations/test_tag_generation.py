"""Standalone orchestration tests; AI transport and semantic replies are mocked."""
from copy import deepcopy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.models import Base, Project, ProjectKnowledge, ProjectQA
from backend.app.integrations.codex import tag_generation as tags
from backend.app.integrations.codex.protocol import CodexError


@pytest.fixture
def saved(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'tags.db'))
    Base.metadata.create_all(engine)
    snapshot = {'project_id': 1, 'publication_id': 'publication', 'items': [],
                'topic_groups': [], 'topic_grouping': {}, 'sources': [{'id': 'source'}]}
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')])
        db.flush()
        db.add_all([ProjectKnowledge(project_id=1, snapshot=snapshot),
                    ProjectKnowledge(project_id=2, snapshot={**snapshot, 'project_id': 2}),
                    ProjectQA(project_id=1, generated_at='qa-time', items=[], sources=[], generation={'model': 'saved'})])
        db.commit()
    monkeypatch.setattr(tags, 'group_records', lambda *a, **k: {'topic_groups': [], 'topic_grouping': {'new': True}})
    yield engine
    engine.dispose()


def snapshot(engine, project_id=1):
    with Session(engine) as db:
        return deepcopy(db.get(ProjectKnowledge, project_id).snapshot)


def test_only_selected_metadata_changes(saved):
    before, other = snapshot(saved), snapshot(saved, 2)
    response = tags.generate_tags(saved, 1, object(), target='knowledge', publication_id='publication')
    assert response['publication_id'] == before['publication_id']
    assert snapshot(saved) == {**before, 'topic_grouping': {'new': True}}
    assert snapshot(saved, 2) == other
    with Session(saved) as db:
        assert db.get(ProjectQA, 1).generation == {'model': 'saved'}


def test_saved_result_change_during_generation_is_conflict(saved, monkeypatch):
    def changed(*a, **k):
        with Session(saved) as db:
            row = db.get(ProjectKnowledge, 1)
            row.snapshot = {**row.snapshot, 'sources': [{'id': 'new-source'}]}
            db.commit()
        return {'topic_groups': [], 'topic_grouping': {'new': True}}
    monkeypatch.setattr(tags, 'group_records', changed)
    with pytest.raises(tags.TagGenerationConflict):
        tags.generate_tags(saved, 1, object(), target='knowledge', publication_id='publication')
    assert snapshot(saved)['topic_grouping'] == {}
    assert snapshot(saved)['sources'] == [{'id': 'new-source'}]


def test_failure_preserves_previous_tags(saved, monkeypatch):
    before = snapshot(saved)
    def failed(*a, **k):
        raise CodexError('transport failed')
    monkeypatch.setattr(tags, 'group_records', failed)
    with pytest.raises(CodexError, match='transport failed'):
        tags.generate_tags(saved, 1, object(), target='knowledge', publication_id='publication')
    assert snapshot(saved) == before


def test_qa_preserves_content_and_generation_identity(saved):
    tags.generate_tags(saved, 1, object(), target='qa', generated_at='qa-time')
    with Session(saved) as db:
        row = db.get(ProjectQA, 1)
        assert (row.generated_at, row.items, row.sources) == ('qa-time', [], [])
        assert row.generation == {'model': 'saved', 'topic_groups': [], 'topic_grouping': {'new': True}}
    with pytest.raises(tags.TagGenerationConflict):
        tags.generate_tags(saved, 1, object(), target='qa', generated_at='foreign-time')


def test_preview_tagging_preserves_publication_and_brief_pair(saved):
    from backend.app.models import GenerationRevision
    from backend.app.integrations.codex.knowledge_previews import read_knowledge_preview, stage_knowledge_preview
    candidate = {**snapshot(saved), 'generated_at': 'preview-time'}
    result = {'published': False, 'publication_id': 'publication', 'knowledge': candidate,
              'brief': {'generated_at': 'preview-time', 'project_state': {'publication_id': 'publication'}},
              'baseline_brief': None, 'diagnostics': {'saved': True}}
    with Session(saved) as db:
        stage_knowledge_preview(db, 1, result)
        db.commit()
        revision_id = db.query(GenerationRevision).one().id
    published_before = snapshot(saved)
    tags.generate_tags(saved, 1, object(), target='knowledge_preview', publication_id='publication')
    with Session(saved) as db:
        preview = read_knowledge_preview(db, 1)
        assert preview == {**result, 'knowledge': {**candidate, 'topic_grouping': {'new': True}}}
        assert db.query(GenerationRevision).one().id == revision_id
    assert snapshot(saved) == published_before


def test_cancelled_tags_preserve_saved_metadata(saved, monkeypatch):
    before = snapshot(saved)
    def cancelled(*a, **k):
        status = tags.progress.read(1, 'tags')
        tags.progress.cancel(1, 'tags', status['started_at'])
        return {'topic_groups': [], 'topic_grouping': {'new': True}}
    monkeypatch.setattr(tags, 'group_records', cancelled)
    with pytest.raises(tags.progress.GenerationCancelled):
        tags.generate_tags(saved, 1, object(), target='knowledge', publication_id='publication')
    assert snapshot(saved) == before
    assert tags.progress.read(1, 'tags')['state'] == 'cancelled'


@pytest.mark.parametrize('setting', [None, '1', '0'])
@pytest.mark.parametrize('count', [0, 24, 121])
def test_optimized_tags_default_and_small_results_avoid_extra_calls(saved, monkeypatch, setting, count):
    monkeypatch.delenv('PROJECTHUB_COMPACT_TAGS', raising=False)
    if setting is not None:
        monkeypatch.setenv('PROJECTHUB_COMPACT_TAGS', setting)
    with Session(saved) as db:
        row = db.get(ProjectKnowledge, 1)
        row.snapshot = {**row.snapshot, 'items': [
            {'id': str(i), 'statement': 'Use SQLite.', 'scope': 'Storage',
             'category': 'DECISION', 'design_status': 'AGREED'} for i in range(count)]}
        db.commit()
    options = {}
    from backend.app.integrations.codex.manager import tag_context_version

    def generate(*args, previous, **kwargs):
        assert bool(tag_context_version()) == (setting != '0')
        options.update(kwargs)
        return {'topic_groups': [], 'topic_grouping': {}}

    monkeypatch.setattr(tags, 'group_records', generate)
    tags.generate_tags(saved, 1, object(), target='knowledge', publication_id='publication')
    assert options == ({'compact_assignment': True} if setting != '0' and count > 120 else {})
    assert tag_context_version() is None
