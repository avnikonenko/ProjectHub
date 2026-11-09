"""Opt-in publication invariants with disposable SQLite and mocked semantic work."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import GenerationRevision, Project, ProjectBrief, ProjectHistoryBatch, ProjectKnowledge
from backend.app.integrations.codex import knowledge_brief as pilot
from backend.app.integrations.codex.brief import SECTION_NAMES
from backend.app.integrations.codex.protocol import CodexError


def _candidate(project_id=1):
    return {
        'project_id': project_id,
        'items': [{'id': 'item-one', 'project_id': project_id, 'statement': 'Use SQLite.',
                   'category': 'DECISION', 'design_status': 'AGREED', 'scope': 'project',
                   'rationale': 'Local operation without another service.', 'source_state': 'valid',
                   'superseded_by': [], 'evidence': [{'source_id': 'source-one', 'role': 'user',
                                                    'excerpt': 'Use SQLite.'}]}],
        'sources': [{'id': 'source-one', 'label': 'User choice', 'text': 'Use SQLite.', 'role': 'user'}],
        'source_manifest': {'fingerprint': 'same', 'entries': []},
        'coverage': {'limitations': []}, 'diagnostics': {'cache_hits': 0, 'ai_requests': 1},
    }


def _brief(publication_id='old', generated_at='old'):
    return {'generated_at': generated_at, 'sections': {}, 'sources': [], 'coverage': [],
            'project_map': {'nodes': [], 'edges': []}, 'generation': {},
            'project_state': {'publication_id': publication_id}}


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add_all([Project(name='One'), Project(name='Two')])
        db.flush()
        for pid in (1, 2):
            candidate = {**_candidate(pid), 'publication_id': 'old', 'generated_at': 'old'}
            db.add(ProjectKnowledge(project_id=pid, snapshot=candidate))
            db.add(ProjectBrief(project_id=pid, **_brief()))
            db.add_all([GenerationRevision(project_id=pid, kind='knowledge', generated_at='old', snapshot=candidate),
                        GenerationRevision(project_id=pid, kind='brief', generated_at='old', snapshot=_brief())])
        db.commit()
    bundle = {'project': {'id': 1, 'name': 'One', 'description': '', 'goals': '', 'repository_path': None},
              'sources': [], 'manifest': {'fingerprint': 'same', 'entries': []}, 'coverage': {'limitations': []}}
    monkeypatch.setattr(pilot, 'load_sources', lambda *a, **kw: deepcopy(bundle))
    monkeypatch.setattr(pilot, 'collect_project_evidence', lambda *a: {'fingerprint': 'repo-baseline'})

    def cached_candidate(engine_arg, project_id, manager, sources, previous=None):
        assert engine_arg is engine and project_id == 1 and previous['publication_id'] == 'old'
        # Successful extraction is deliberately committed before synthesis/publish.
        with Session(engine) as db:
            if db.get(ProjectHistoryBatch, (1, 'successful-extraction')) is None:
                db.add(ProjectHistoryBatch(project_id=1, fingerprint='successful-extraction', evidence=[{'raw': 'cached'}]))
                db.commit()
        return _candidate()

    monkeypatch.setattr(pilot, 'build_candidate', cached_candidate)
    yield SimpleNamespace(engine=engine, bundle=bundle)
    engine.dispose()


def _stored(engine, project_id=1):
    with Session(engine) as db:
        knowledge = deepcopy(db.get(ProjectKnowledge, project_id).snapshot)
        brief = deepcopy(pilot.read_brief(db, project_id))
        revisions = [(r.kind, r.generated_at, deepcopy(r.snapshot)) for r in db.scalars(
            select(GenerationRevision).where(GenerationRevision.project_id == project_id).order_by(GenerationRevision.id))]
    return knowledge, brief, revisions


class FakeSynthesis:
    generation_metadata = {'account': 'mock', 'model': 'mock', 'effort': 'low'}
    fail = False

    def generate_brief(self, prompt, schema):
        if self.fail:
            raise CodexError('Mock synthesis unavailable')
        assert 'Local operation without another service.' in prompt
        reference = schema['properties']['sections']['properties']['overview']['items']['properties']['source_ids']['items']['enum'][0]
        sections = {key: [] for key in SECTION_NAMES}
        for key in ('overview', 'architecture', 'current_status'):
            sections[key] = [{'text': 'Use SQLite for a local project.', 'source_ids': [reference],
                              'classification': 'reported'}]
        return json.dumps({'sections': sections, 'project_map': {'nodes': [], 'edges': []}})


def test_preview_preserves_published_pair_but_keeps_successful_extraction(prepared):
    before = _stored(prepared.engine)
    result = pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis())
    assert result['published'] is False
    assert result['knowledge']['publication_id'] == result['brief']['project_state']['publication_id'] == result['publication_id']
    assert _stored(prepared.engine) == before
    with Session(prepared.engine) as db:
        assert db.get(ProjectHistoryBatch, (1, 'successful-extraction')) is not None


def test_publish_updates_knowledge_brief_and_both_revisions_with_one_identity(prepared):
    foreign_before = _stored(prepared.engine, 2)
    result = pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis(), publish=True)
    knowledge, brief, revisions = _stored(prepared.engine)
    assert result['published'] is True
    assert knowledge['publication_id'] == brief['project_state']['publication_id'] == result['publication_id']
    assert revisions[-2][0] == 'knowledge' and revisions[-1][0] == 'brief'
    assert revisions[-2][2]['publication_id'] == revisions[-1][2]['project_state']['publication_id'] == result['publication_id']
    assert _stored(prepared.engine, 2) == foreign_before


def test_database_failure_rolls_back_both_current_records_and_revisions(prepared):
    before = _stored(prepared.engine)
    with prepared.engine.begin() as connection:
        connection.execute(text("CREATE TRIGGER fail_brief_revision BEFORE INSERT ON generation_revisions "
                                "WHEN NEW.kind='brief' AND NEW.generated_at!='old' "
                                "BEGIN SELECT RAISE(ABORT, 'injected publication failure'); END"))
    with pytest.raises(IntegrityError, match='injected publication failure'):
        pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis(), publish=True)
    assert _stored(prepared.engine) == before
    with Session(prepared.engine) as db:
        assert db.get(ProjectHistoryBatch, (1, 'successful-extraction')) is not None


def test_source_change_during_generation_prevents_publication(prepared, monkeypatch):
    before = _stored(prepared.engine)
    calls = 0
    def changing_sources(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = deepcopy(prepared.bundle)
        if calls > 1:
            result['manifest']['fingerprint'] = 'changed'
        return result
    monkeypatch.setattr(pilot, 'load_sources', changing_sources)
    with pytest.raises(CodexError, match='changed during generation'):
        pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis(), publish=True)
    assert _stored(prepared.engine) == before


def test_final_generation_failure_preserves_pair_and_completed_cache(prepared):
    before = _stored(prepared.engine)
    manager = FakeSynthesis()
    manager.fail = True
    with pytest.raises(CodexError, match='Mock synthesis unavailable'):
        pilot.generate_knowledge_brief(prepared.engine, 1, manager, publish=True)
    assert _stored(prepared.engine) == before
    with Session(prepared.engine) as db:
        assert db.get(ProjectHistoryBatch, (1, 'successful-extraction')) is not None
    # A failed run must release its shared lock so the next request can proceed.
    assert pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis())['published'] is False


@pytest.mark.parametrize('change', [
    lambda candidate, brief: candidate.update(project_id=2),
    lambda candidate, brief: brief['project_state'].update(publication_id='different'),
    lambda candidate, brief: brief.update(generated_at='different'),
])
def test_publication_rejects_wrong_project_or_mismatched_pair_identity(prepared, change):
    before = _stored(prepared.engine)
    candidate = {**_candidate(), 'publication_id': 'new', 'generated_at': 'new'}
    brief = _brief('new', 'new')
    change(candidate, brief)
    with pytest.raises(CodexError, match='publication identities'):
        pilot.publish_pair(prepared.engine, 1, candidate, brief, prepared.bundle['manifest'])
    assert _stored(prepared.engine) == before


def test_historical_agreement_is_not_reclassified_or_exposed_as_current_evidence(prepared):
    candidate = _candidate()
    candidate['items'][0]['source_state'] = 'unavailable'
    sources, groups = pilot._brief_inputs(candidate, prepared.bundle, {'sources': []})
    assert candidate['items'][0]['design_status'] == 'AGREED'
    assert candidate['items'][0]['source_state'] == 'unavailable'
    assert all(source['kind'] != 'extracted_interpretation' for source in sources if 'kind' in source)
    assert set(groups) == {'project:metadata'}


def test_repository_change_during_synthesis_preserves_published_pair(prepared, monkeypatch):
    before = _stored(prepared.engine)
    prepared.bundle['project']['repository_path'] = '/not-executed/mock-repository'
    calls = 0
    def code_evidence(path):
        nonlocal calls
        calls += 1
        return {'sources': [{'id': 'code-one', 'label': 'Example source', 'text': f'class Version{calls}: pass'}],
                'coverage': []}
    monkeypatch.setattr(pilot, 'collect_repository_code', code_evidence)
    with pytest.raises(CodexError, match='repository evidence changed'):
        pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis(), publish=True)
    assert _stored(prepared.engine) == before


def test_unavailable_historical_choices_are_retained_with_visible_coverage(prepared, monkeypatch):
    def candidate(*args, **kwargs):
        value = _candidate()
        value['items'][0]['source_state'] = 'unavailable'
        return value
    monkeypatch.setattr(pilot, 'build_candidate', candidate)
    class Synthesis:
        def generate_brief(self, prompt, schema):
            assert 'historical or conflicting interpretations need rechecking' in prompt
            sources = schema['properties']['sections']['properties']['overview']['items']['properties']['source_ids']['items']['enum']
            assert sources == ['project:metadata']
            sections = {name: [] for name in SECTION_NAMES}
            for name in ('overview', 'architecture', 'current_status'):
                sections[name] = [{'text': 'Source evidence needs checking.', 'source_ids': sources, 'classification': 'reported'}]
            return json.dumps({'sections': sections, 'project_map': {'nodes': [], 'edges': []}})
    result = pilot.generate_knowledge_brief(prepared.engine, 1, Synthesis())
    assert result['knowledge']['items'][0]['design_status'] == 'AGREED'
    assert any('need rechecking' in line for line in result['brief']['coverage'])


def test_synthesis_does_not_repeat_raw_quotes_and_window_manifests(prepared):
    candidate = _candidate()
    candidate['items'][0]['context_dependencies'] = [{'source_id': 'x', 'revision': 'large-window-marker'}] * 200
    sources, groups = pilot._brief_inputs(candidate, prepared.bundle, {'sources': []})
    item = sources[0]['item']
    assert item['rationale'] == candidate['items'][0]['rationale']
    assert 'context_dependencies' not in item and 'evidence' not in item
    assert groups[sources[0]['id']][0]['text'] == candidate['sources'][0]['text']


def test_presentation_suppression_preserves_required_sections_distinct_scope_and_conflicts():
    sections = {name: [] for name in SECTION_NAMES}
    agreed = {'text': 'Support docking and MD.', 'classification': 'reported', 'source_ids': ['scope']}
    mvp = {'text': 'First-release docking scope remains undecided.', 'classification': 'unresolved', 'source_ids': ['mvp']}
    conflict = {'text': 'Support docking and MD.', 'classification': 'unresolved', 'source_ids': ['conflicting-source']}
    sections['overview'] = [deepcopy(agreed)]
    sections['current_status'] = [deepcopy(agreed)]
    sections['architecture'] = [{'text': 'Use independent adapters.', 'classification': 'reported', 'source_ids': ['architecture']}]
    sections['changes'] = [deepcopy(agreed)]
    sections['unresolved_problems'] = [mvp, conflict]
    before = deepcopy(sections)
    shown = pilot.suppress_repeated_points(sections)
    assert sections == before
    assert shown['changes'] == []
    assert shown['overview'] == shown['current_status'] == [agreed]
    assert shown['unresolved_problems'] == [mvp, conflict]


def test_synthesis_suppresses_repetition_without_mutating_knowledge_or_evidence(prepared):
    candidate = _candidate()
    before = deepcopy(candidate)
    class RepeatedSynthesis(FakeSynthesis):
        def generate_brief(self, prompt, schema):
            assert 'Suppress redundancy only in presentation' in prompt
            assert 'A broad agreement does not settle an unresolved first-release boundary' in prompt
            result = json.loads(super().generate_brief(prompt, schema))
            result['sections']['changes'] = deepcopy(result['sections']['overview'])
            return json.dumps(result)
    sections, graph, cited = pilot._synthesize(RepeatedSynthesis(), candidate, prepared.bundle, {'sources': []})
    assert sections['changes'] == []
    assert sections['overview'] and sections['architecture'] and sections['current_status']
    assert candidate == before
    assert cited[0]['text'] == candidate['sources'][0]['text']
