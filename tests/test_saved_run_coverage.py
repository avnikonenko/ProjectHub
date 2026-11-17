"""Saved-run measurements survive reloads without borrowing newer evidence.

SQLite and generation orchestration are real; all semantic/model work is mocked.
"""
from copy import deepcopy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, ProjectBrief, ProjectQA, ProjectKnowledge, GenerationRevision
from backend.app.integrations.codex import brief, qa, knowledge_brief, progress
from backend.app.integrations.codex.generation_metrics import begin_model_request
from backend.app.integrations.codex.protocol import CodexError
from backend.tests.integrations.test_brief import FakeCodex, project_db
from backend.tests.integrations.test_knowledge_brief import prepared, FakeSynthesis


def measured_request(identifier):
    recorder = begin_model_request(identifier)
    assert recorder is not None
    recorder.bind_turn('turn')
    recorder.record({'threadId': identifier, 'turnId': 'turn', 'tokenUsage': {'total': {
        'totalTokens': 15, 'inputTokens': 10, 'outputTokens': 5,
        'cachedInputTokens': 0, 'reasoningOutputTokens': 0}}})


def test_legacy_brief_saves_its_run_not_later_progress(project_db, monkeypatch):
    class Generator(FakeCodex):
        calls = 0
        def generate_brief(self, prompt, schema):
            self.calls += 1
            measured_request(str(self.calls))
            return super().generate_brief(prompt, schema)
    manager = Generator()
    saved = brief.refresh_brief(project_db, 1, manager)
    diagnostics = saved['diagnostics']
    assert diagnostics['duration_seconds'] >= 0
    assert diagnostics['input_records'] > 0 and diagnostics['input_chars'] > 0
    assert diagnostics['cited_source_count'] == len(saved['sources'])
    assert diagnostics['model_requests'] == manager.calls
    assert diagnostics['token_usage']['total_tokens'] == manager.calls * 15
    with Session(project_db) as db:
        revision = db.scalars(select(GenerationRevision).where(GenerationRevision.kind == 'brief')).one()
        assert revision.snapshot['diagnostics'] == diagnostics
        assert db.get(ProjectBrief, 1).generation['diagnostics'] == diagnostics
    # A read after application restart must never replace saved counters with
    # whatever another run is doing now.
    monkeypatch.setattr(progress, 'read', lambda *args: pytest.fail('Read borrowed current progress'))
    project_db.dispose()
    with Session(project_db) as db:
        assert brief.read_brief(db, 1) == saved
        assert brief.read_brief(db, 2) is None


def test_qa_persists_coverage_and_diagnostics_with_each_revision(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='One')); db.commit()
    evidence = [{'text': 'Use SQLite.', 'sources': [{'id': 'original', 'text': 'Use SQLite.'}]}]
    coverage = ['One linked session could not be read.']
    monkeypatch.setattr(qa, 'archive_history', lambda *args: (deepcopy(evidence), list(coverage)))
    class Generator:
        generation_metadata = {'account': 'fixture', 'model': 'fixture', 'effort': 'low'}
        calls = 0
        fail = False
        def generate_brief(self, prompt, schema):
            self.calls += 1
            measured_request(str(self.calls))
            if self.fail:
                raise CodexError('Synthetic generation failure')
            return json.dumps({'items': [{'question': 'Which database?', 'answer': 'SQLite.',
                                          'source_ids': ['1'], **({'variants': []}
                                          if 'variants' in schema['properties']['items']['items']['properties'] else {})}]})
    manager = Generator()
    try:
        saved = qa.refresh_qa(engine, 1, manager)
        assert saved['coverage'][0] == coverage[0]
        assert saved['diagnostics']['input_records'] == 1
        assert saved['diagnostics']['input_chars'] == len('Use SQLite.')
        assert saved['diagnostics']['model_requests'] == manager.calls == 2
        assert saved['generation'] == manager.generation_metadata
        assert 'diagnostics' not in manager.generation_metadata
        with Session(engine) as db:
            revision = db.scalars(select(GenerationRevision)).one()
            assert revision.snapshot['coverage'] == saved['coverage']
            assert revision.snapshot['diagnostics'] == saved['diagnostics']
        manager.fail = True
        with pytest.raises(CodexError):
            qa.refresh_qa(engine, 1, manager)
        engine.dispose()
        monkeypatch.setattr(progress, 'read', lambda *args: pytest.fail('Read borrowed current progress'))
        with Session(engine) as db:
            assert qa.read_qa(db, 1) == saved
    finally:
        engine.dispose()


@pytest.mark.parametrize('metadata', [None, [], {}, {'diagnostics': 'bad', 'coverage': 'bad'},
                                      {'diagnostics': None, 'coverage': [1]}])
def test_old_or_malformed_optional_metadata_is_not_invented(tmp_path, metadata, monkeypatch):
    engine, _ = initialize(tmp_path)
    try:
        with Session(engine) as db:
            db.add(Project(name='Legacy')); db.flush()
            db.add_all([ProjectBrief(project_id=1, generated_at='old', sections={}, sources=[],
                                    coverage=['Original coverage'], generation=metadata),
                        ProjectQA(project_id=1, generated_at='old', items=[], sources=[], generation=metadata)])
            db.commit()
        monkeypatch.setattr(progress, 'read', lambda *args: pytest.fail('Legacy read borrowed progress'))
        with Session(engine) as db:
            old_brief, old_qa = brief.read_brief(db, 1), qa.read_qa(db, 1)
            assert 'diagnostics' not in old_brief and 'diagnostics' not in old_qa
            assert 'coverage' not in old_qa
            assert old_brief['coverage'] == ['Original coverage']
    finally:
        engine.dispose()


def test_knowledge_preview_and_separate_published_summary_keep_same_recorded_run(prepared):
    manager = FakeSynthesis()
    original_metadata = deepcopy(manager.generation_metadata)
    result = knowledge_brief.generate_knowledge_brief(prepared.engine, 1, manager)
    assert result['brief']['diagnostics'] == result['diagnostics']
    recorded = result['knowledge']['diagnostics']
    assert {key: value for key, value in recorded.items() if key != 'evidence_coverage'} == {
        key: value for key, value in result['diagnostics'].items() if key != 'evidence_coverage'}
    assert recorded['evidence_coverage']['repository']['status'] == 'not_used'
    assert result['brief']['diagnostics']['evidence_coverage']['repository']['status'] == 'not_linked'
    assert recorded['evidence_coverage']['conversations'] == result['diagnostics']['evidence_coverage']['conversations']
    assert manager.generation_metadata == original_metadata
    with Session(prepared.engine) as db:
        original_overview = brief.read_brief(db, 1)
    knowledge_brief.publish_pair(prepared.engine, 1, result['knowledge'], result['brief'],
                                 prepared.bundle['manifest'])
    with Session(prepared.engine) as db:
        current = db.get(ProjectKnowledge, 1).snapshot['summary']
        assert current['diagnostics'] == result['diagnostics']
        assert current['generation'] == original_metadata
        assert brief.read_brief(db, 1) == original_overview
        revision = db.scalars(select(GenerationRevision).where(
            GenerationRevision.project_id == 1, GenerationRevision.kind == 'knowledge').order_by(
                GenerationRevision.id.desc())).first()
        assert revision.snapshot['summary']['diagnostics'] == result['diagnostics']
        assert revision.snapshot['diagnostics']['evidence_coverage']['repository']['status'] == 'not_used'
