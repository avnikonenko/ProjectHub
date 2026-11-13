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
            payload = json.loads(prompt.split('Return only the required structured JSON.\n', 1)[1])
            assert sources == [payload['sources'][0]['id']]
            assert len(payload['sources']) == 1
            assert payload['sources'][0]['label'] == 'User-maintained project metadata'
            sections = {name: [] for name in SECTION_NAMES}
            for name in ('overview', 'architecture', 'current_status'):
                sections[name] = [{'text': 'Source evidence needs checking.', 'source_ids': sources, 'classification': 'reported'}]
            return json.dumps({'sections': sections, 'project_map': {'nodes': [], 'edges': []}})
    result = pilot.generate_knowledge_brief(prepared.engine, 1, Synthesis())
    assert result['knowledge']['items'][0]['design_status'] == 'AGREED'
    assert any('need rechecking' in line for line in result['brief']['coverage'])


def test_synthesis_includes_selected_quotes_without_window_manifests(prepared):
    candidate = _candidate()
    candidate['items'][0]['context_dependencies'] = [{'source_id': 'x', 'revision': 'large-window-marker'}] * 200
    sources, groups = pilot._brief_inputs(candidate, prepared.bundle, {'sources': []})
    item = sources[0]['item']
    assert item['rationale'] == candidate['items'][0]['rationale']
    assert 'context_dependencies' not in item
    assert item['evidence'][0]['excerpt'] == 'Use SQLite.'
    assert 'revision' not in item['evidence'][0]
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


def test_synthesis_receives_acceptance_evidence_and_scope(prepared):
    candidate = _candidate()
    candidate['items'][0]['agreement_basis'] = 'direct_user'
    before = deepcopy(candidate)
    class EvidenceSynthesis(FakeSynthesis):
        def generate_brief(self, prompt, schema):
            payload = json.loads(prompt.split('Return only the required structured JSON.\n', 1)[1])
            item = payload['sources'][0]['item']
            assert item['scope'] == 'project'
            assert item['agreement_basis'] == 'direct_user'
            quote_id = item['evidence'][0]['excerpt_id']
            assert payload['evidence_excerpts'][quote_id] == 'Use SQLite.'
            assert payload['evidence_sources'][item['evidence'][0]['source_ref']]['role'] == 'user'
            assert 'Never call an initial scope accepted' in prompt
            return super().generate_brief(prompt, schema)
    pilot._synthesize(EvidenceSynthesis(), candidate, prepared.bundle, {'sources': []})
    assert candidate == before


def test_visible_citations_are_bounded_but_all_support_is_retained(prepared):
    candidate = _candidate()
    item = candidate['items'][0]
    # Assistant context precedes the decisive user instruction in storage.
    for index in range(4):
        identifier = f'assistant-{index}'
        candidate['sources'].insert(0, {'id': identifier, 'text': 'Use SQLite.', 'role': 'assistant'})
        item['evidence'].insert(0, {'source_id': identifier, 'role': 'assistant', 'excerpt': 'Use SQLite.'})
    before = deepcopy(candidate)
    sections, _, cited = pilot._synthesize(FakeSynthesis(), candidate, prepared.bundle, {'sources': []})
    for entry in sections['overview']:
        assert len(entry['source_ids']) == 3
        assert entry['source_ids'][0] == 'source-one'
        assert len(entry['supporting_source_ids']) == 5
    assert len(cited) == 5
    assert candidate == before


def test_compound_agreements_keep_each_proposal_and_acceptance(prepared):
    candidate = _candidate()
    candidate['items'] = []
    candidate['sources'] = []
    for index in range(3):
        proposal, acceptance = f'proposal-{index}', f'acceptance-{index}'
        item = deepcopy(_candidate()['items'][0])
        item.update(id=f'item-{index}', agreement_basis='proposal_acceptance', evidence=[
            {'source_id': proposal, 'role': 'assistant', 'excerpt': f'Use option {index}?'},
            {'source_id': acceptance, 'role': 'user', 'excerpt': 'Agree.'}])
        candidate['items'].append(item)
        candidate['sources'].extend([
            {'id': proposal, 'text': f'Use option {index}?', 'role': 'assistant'},
            {'id': acceptance, 'text': 'Agree.', 'role': 'user'}])
    before = deepcopy(candidate)
    class CompoundSynthesis(FakeSynthesis):
        def generate_brief(self, prompt, schema):
            result = json.loads(super().generate_brief(prompt, schema))
            refs = schema['properties']['sections']['properties']['overview']['items']['properties']['source_ids']['items']['enum'][:3]
            result['sections']['overview'][0]['source_ids'] = refs
            return json.dumps(result)
    sections, _, cited = pilot._synthesize(CompoundSynthesis(), candidate, prepared.bundle, {'sources': []})
    ids = sections['overview'][0]['source_ids']
    assert len(ids) == 6  # Evidence context takes precedence over the soft limit.
    for index in range(3):
        assert f'proposal-{index}' in ids and f'acceptance-{index}' in ids
    assert len(cited) == 6
    assert candidate == before


def test_citations_keep_preceding_proposal_despite_later_rationale(prepared):
    candidate = _candidate()
    refs = [('reason', 'user', 0), ('later', 'assistant', 4),
            ('proposal', 'assistant', 1), ('acceptance', 'user', 2)]
    candidate['items'][0].update(agreement_basis='proposal_acceptance', evidence=[
        {'source_id': name, 'role': role, 'order': order, 'conversation_id': 'same', 'excerpt': name}
        for name, role, order in refs])
    candidate['sources'] = [{'id': name, 'role': role, 'text': name} for name, role, _ in refs]
    before = deepcopy(candidate)
    sections, _, _ = pilot._synthesize(FakeSynthesis(), candidate, prepared.bundle, {'sources': []})
    assert 'proposal' in sections['overview'][0]['source_ids']
    assert 'acceptance' in sections['overview'][0]['source_ids']
    assert candidate == before


def test_shared_quotes_do_not_exhaust_synthesis_budget(prepared):
    candidate = _candidate()
    refs = [{'source_id': f'quote-source-{index}', 'role': 'assistant',
             'excerpt': str(index) + 'x' * 1599} for index in range(6)]
    candidate['items'] = [{**deepcopy(candidate['items'][0]), 'id': f'item-{index}',
                           'design_status': 'PROPOSED', 'agreement_basis': 'none', 'evidence': deepcopy(refs)}
                          for index in range(20)]
    candidate['sources'] = [{'id': ref['source_id'], 'role': ref['role'], 'text': ref['excerpt']} for ref in refs]
    before = deepcopy(candidate)
    class CompactSynthesis(FakeSynthesis):
        def generate_brief(self, prompt, schema):
            payload = json.loads(prompt.split('Return only the required structured JSON.\n', 1)[1])
            assert len(payload['evidence_excerpts']) == 6
            assert len(json.dumps(payload)) < pilot.MAX_SYNTHESIS_CHARS
            for source in payload['sources']:
                for evidence in source.get('item', {}).get('evidence', []):
                    assert payload['evidence_excerpts'][evidence['excerpt_id']] in [ref['excerpt'] for ref in refs]
            return super().generate_brief(prompt, schema)
    pilot._synthesize(CompactSynthesis(), candidate, prepared.bundle, {'sources': []})
    assert candidate == before


def test_synthesis_omits_only_empty_optional_fields_without_mutating_knowledge(prepared):
    candidate = _candidate()
    first = candidate['items'][0]
    first.update(alternatives='', limitations=None, conflicts_with=[], relationships=[])
    second = {**deepcopy(first), 'id': 'second-item',
              'rationale': 'Preserve the original technical reason.',
              'alternatives': 'A remote database was considered.',
              'limitations': 'Only local execution has been discussed.',
              'conflicts_with': ['item-one'],
              'relationships': [{'kind': 'conflict', 'target': 'item-one', 'status': 'interpretation'}]}
    candidate['items'].append(second)
    before = deepcopy(candidate)

    class PreservedSynthesis(FakeSynthesis):
        def generate_brief(self, prompt, schema):
            payload = json.loads(prompt.split('Return only the required structured JSON.\n', 1)[1])
            views = [source['item'] for source in payload['sources'] if 'item' in source]
            for field in ('alternatives', 'limitations', 'conflicts_with', 'relationships'):
                assert field not in views[0]
            for original, view in zip(candidate['items'], views):
                for field in ('statement', 'category', 'design_status', 'scope', 'source_state', 'rationale'):
                    assert view[field] == original[field]
                assert view['agreement_basis'] == original.get('agreement_basis', 'none')
                for evidence, retained in zip(original['evidence'], view['evidence']):
                    metadata = payload['evidence_sources'][retained['source_ref']]
                    assert metadata['source_id'] == evidence['source_id']
                    assert metadata['role'] == evidence['role']
                    assert payload['evidence_excerpts'][retained['excerpt_id']] == evidence['excerpt']
            for field in ('alternatives', 'limitations', 'conflicts_with', 'relationships'):
                assert views[1][field] == second[field]
            return super().generate_brief(prompt, schema)

    pilot._synthesize(PreservedSynthesis(), candidate, prepared.bundle, {'sources': []})
    assert candidate == before


def test_synthesis_input_guard_uses_compact_json_and_still_enforces_limit(prepared, monkeypatch):
    candidate = _candidate()
    candidate['items'][0]['limitations'] = 'Préserve the recorded caveat.'
    before = deepcopy(candidate)

    class CapturedSynthesis(FakeSynthesis):
        calls = 0
        serialized = ''

        def generate_brief(self, prompt, schema):
            self.calls += 1
            self.serialized = prompt.split('Return only the required structured JSON.\n', 1)[1]
            payload = json.loads(self.serialized)
            assert self.serialized == json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
            assert 'Préserve' in self.serialized
            return super().generate_brief(prompt, schema)

    manager = CapturedSynthesis()
    pilot._synthesize(manager, candidate, prepared.bundle, {'sources': []})
    compact_size = len(manager.serialized)
    spaced_size = len(json.dumps(json.loads(manager.serialized), ensure_ascii=False))
    assert compact_size < spaced_size
    # The exact compact boundary fits even though ordinary JSON would not.
    monkeypatch.setattr(pilot, 'MAX_SYNTHESIS_CHARS', compact_size)
    pilot._synthesize(manager, candidate, prepared.bundle, {'sources': []})
    assert manager.calls == 2
    monkeypatch.setattr(pilot, 'MAX_SYNTHESIS_CHARS', compact_size - 1)
    with pytest.raises(CodexError, match='synthesis input limit'):
        pilot._synthesize(manager, candidate, prepared.bundle, {'sources': []})
    assert manager.calls == 2
    assert candidate == before
