"""Independent staged Overview integration checks: real SQLite, mocked model output.

Extraction/source adapters are deterministic fixtures. The production generation,
size routing, stage validation/cache, citation restoration and persistence run.
These checks do not measure model recall or scientific/semantic correctness.
"""
from copy import deepcopy
import json
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.integrations.codex import knowledge_brief as pilot, progress
from backend.app.integrations.codex.brief import SECTION_NAMES
from backend.app.integrations.codex.knowledge_previews import read_knowledge_preview, stage_knowledge_preview
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import ProjectHistoryBatch
from backend.tests.integrations.test_knowledge_brief import prepared, _candidate, _stored, _topic_reply


def _large_candidate(project_id, count=520):
    result = _candidate(project_id)
    result['items'], result['sources'] = [], []
    for index in range(count):
        identifier = f'original:{project_id}:{index}'
        quote = f'SOURCE_{project_id}_{index}: Adopt the independent component {index} constraint.'
        result['sources'].append({'id': identifier, 'label': f'Original source {index}',
                                  'text': quote, 'excerpts': [quote], 'role': 'user',
                                  'conversation_id': f'conversation:{project_id}:{index}', 'order': 1})
        result['items'].append({
            'id': f'item:{index}', 'project_id': project_id,
            'statement': f'Independent component {index}. ' + 'Preserve this project-specific requirement. ' * 16,
            'category': 'REQUIREMENT', 'design_status': 'AGREED', 'scope': f'component:{index}',
            'rationale': f'Component {index} must retain its independent constraint.',
            'source_state': 'valid', 'superseded_by': [], 'agreement_basis': 'direct_user',
            'evidence': [{'source_id': identifier, 'role': 'user', 'excerpt': quote,
                          'conversation_id': f'conversation:{project_id}:{index}', 'order': 1}],
        })
    return result


def _saved_preview(engine, project_id=1):
    with Session(engine) as db:
        return read_knowledge_preview(db, project_id)


def _stage_rows(engine, project_id=1):
    with Session(engine) as db:
        rows = list(db.scalars(select(ProjectHistoryBatch).where(
            ProjectHistoryBatch.project_id == project_id,
            ProjectHistoryBatch.fingerprint != 'successful-extraction').order_by(ProjectHistoryBatch.fingerprint)))
        return [row for row in rows if isinstance(row.evidence, list) and row.evidence
                and 'stage_response' in row.evidence[0]]


@pytest.fixture
def staged_state(prepared, monkeypatch):
    candidates = {project_id: _large_candidate(project_id) for project_id in (1, 2)}
    originals = deepcopy(candidates)
    bundles = {}
    for project_id in (1, 2):
        bundle = deepcopy(prepared.bundle)
        bundle['project'].update(id=project_id, name=f'Project {project_id}', repository_path='/read-only/mock-repository')
        bundle['sources'] = deepcopy(candidates[project_id]['sources'])
        bundles[project_id] = bundle
        with Session(prepared.engine) as db:
            preview = {'published': False, 'publication_id': 'prior-preview',
                       'knowledge': {'project_id': project_id, 'publication_id': 'prior-preview',
                                     'generated_at': 'prior-preview', 'items': []},
                       'brief': {'generated_at': 'prior-preview',
                                 'project_state': {'publication_id': 'prior-preview'}},
                       'baseline_brief': None, 'diagnostics': {}}
            stage_knowledge_preview(db, project_id, preview)
            db.commit()
    repository = {'sources': [{'id': 'file:core.py', 'label': 'core.py',
                              'text': 'CODE_ORIGINAL: the inspected core module declaration.'}], 'coverage': []}
    monkeypatch.setattr(pilot, 'load_sources', lambda engine, pid, **kw: deepcopy(bundles[pid]))
    monkeypatch.setattr(pilot, 'collect_repository_code', lambda path: deepcopy(repository))
    monkeypatch.setattr(pilot, 'build_candidate', lambda engine, pid, *a, **kw: deepcopy(candidates[pid]))
    monkeypatch.setattr(pilot, 'ai_worker_settings', lambda: {'generation_profile': 'quality', 'extraction_workers': 1})
    monkeypatch.setenv('PROJECTHUB_GENERATION_BUDGET', '900')
    yield SimpleNamespace(engine=prepared.engine, candidates=candidates, originals=originals,
                          bundles=bundles, repository=repository)
    assert candidates == originals, 'Generation mutated the original Knowledge fixture.'


class StagedModel:
    generation_metadata = {'account': 'mock-subscription', 'model': 'model-a', 'effort': 'low'}

    def __init__(self, *, fail_at=None, cancel_at=None, foreign_at=None, project_id=1, model='model-a'):
        self.calls = []
        self.grouping_calls = 0
        self.seen = set()
        self.fail_at, self.cancel_at, self.foreign_at = fail_at, cancel_at, foreign_at
        self.project_id = project_id
        self.generation_model, self.generation_effort = model, 'low'
        self.generation_account = 'mock-subscription'
        self.generation_metadata = {**self.generation_metadata, 'model': model}

    def generate_brief(self, prompt, schema):
        topic_reply = _topic_reply(prompt, schema)
        if topic_reply is not None:
            self.grouping_calls += 1
            return topic_reply
        # Production prompts end with their serialized evidence object.
        encoded = prompt.rsplit('\n', 1)[-1]
        payload = json.loads(encoded)
        assert len(encoded) <= pilot.MAX_SYNTHESIS_CHARS
        assert 'overview_selection' not in payload or not payload['overview_selection'].get('partial')
        self.calls.append(deepcopy(payload))
        self.seen.update(re.findall(r'SOURCE_\d+_\d+', encoded))
        number = len(self.calls)
        if number == self.fail_at:
            raise CodexError('Injected stage model failure')
        if number == self.cancel_at:
            status = progress.read(self.project_id, 'knowledge')
            progress.cancel(self.project_id, 'knowledge', status['started_at'])
            raise progress.GenerationCancelled()
        allowed = schema['properties']['sections']['properties']['overview']['items']['properties']['source_ids']['items']['enum']
        sections = {name: [] for name in SECTION_NAMES}
        for section in ('overview', 'current_status', 'architecture'):
            sections[section] = [{'text': f'A supported synthetic {section} point.',
                                  'classification': 'reported',
                                  'source_ids': ['foreign:project:999'] if number == self.foreign_at else [allowed[0]]}]
        return json.dumps({'sections': sections, 'project_map': {'nodes': [], 'edges': []}})


def test_oversized_refresh_processes_every_source_and_preserves_originals(staged_state):
    before = _stored(staged_state.engine)
    other_before = _stored(staged_state.engine, 2)
    manager = StagedModel()
    result = pilot.generate_knowledge_brief(staged_state.engine, 1, manager)
    assert len(manager.calls) > 1
    assert manager.seen == {f'SOURCE_1_{index}' for index in range(520)}
    assert any('CODE_ORIGINAL' in json.dumps(payload) for payload in manager.calls)
    original_ids = {source['id'] for source in staged_state.candidates[1]['sources']} | {'file:core.py', 'project:metadata'}
    assert result['brief']['sources']
    assert all(source['id'] in original_ids for source in result['brief']['sources'])
    originals = {source['id']: source['text'] for source in staged_state.candidates[1]['sources']}
    for source in result['brief']['sources']:
        if source['id'] in originals:
            assert source['text'] == originals[source['id']]
        assert 'A supported synthetic' not in source.get('text', '')
    assert len(result['knowledge']['items']) == 520
    assert _stored(staged_state.engine) == before
    assert _stored(staged_state.engine, 2) == other_before
    assert _saved_preview(staged_state.engine) == result
    assert _saved_preview(staged_state.engine, 2)['publication_id'] == 'prior-preview'


@pytest.mark.parametrize('failure', ['model', 'cancel', 'foreign'])
@pytest.mark.parametrize('publish', [False, True])
def test_failed_stage_preserves_preview_pair_and_reuses_completed_cache(staged_state, failure, publish):
    before = _stored(staged_state.engine)
    preview = _saved_preview(staged_state.engine)
    kwargs = {'fail_at': 2} if failure == 'model' else {'cancel_at': 2} if failure == 'cancel' else {'foreign_at': 2}
    manager = StagedModel(**kwargs)
    with pytest.raises(CodexError):
        pilot.generate_knowledge_brief(staged_state.engine, 1, manager, publish=publish)
    assert _stored(staged_state.engine) == before
    assert _saved_preview(staged_state.engine) == preview
    assert _stage_rows(staged_state.engine), 'Earlier successful stage cache was lost.'
    assert progress.read(1, 'knowledge')['state'] == ('cancelled' if failure == 'cancel' else 'failed')
    retry = StagedModel()
    result = pilot.generate_knowledge_brief(staged_state.engine, 1, retry)
    assert result['published'] is False
    assert len(retry.calls) > 0
    assert result['diagnostics']['synthesis_cache_hits'] >= 1
    assert result['diagnostics']['synthesis_requests'] == len(retry.calls)



def test_stage_cache_is_project_and_model_scoped(staged_state, monkeypatch):
    initial = StagedModel()
    pilot.generate_knowledge_brief(staged_state.engine, 1, initial)
    first_cache = len(_stage_rows(staged_state.engine))
    cached = StagedModel()
    pilot.generate_knowledge_brief(staged_state.engine, 1, cached)
    assert len(cached.calls) < len(initial.calls)
    alternate = StagedModel(model='model-b')
    pilot.generate_knowledge_brief(staged_state.engine, 1, alternate)
    assert len(alternate.calls) == len(initial.calls)
    assert len(_stage_rows(staged_state.engine)) > first_cache
    # Keep the wire evidence identical so project isolation cannot pass merely
    # because project names, quotations or local source IDs differ.
    other_candidate = deepcopy(staged_state.candidates[1])
    other_candidate['project_id'] = 2
    for item in other_candidate['items']:
        item['project_id'] = 2
    other_bundle = deepcopy(staged_state.bundles[1])
    other_bundle['project']['id'] = 2
    monkeypatch.setattr(pilot, 'build_candidate', lambda engine, pid, *a, **kw:
                        deepcopy(other_candidate if pid == 2 else staged_state.candidates[1]))
    monkeypatch.setattr(pilot, 'load_sources', lambda engine, pid, **kw:
                        deepcopy(other_bundle if pid == 2 else staged_state.bundles[1]))
    foreign = StagedModel(project_id=2)
    pilot.generate_knowledge_brief(staged_state.engine, 2, foreign)
    assert len(foreign.calls) == len(initial.calls)
    assert _stage_rows(staged_state.engine, 2)


@pytest.mark.parametrize('corruption', ['malformed', 'foreign_reference'])
def test_invalid_cached_stage_is_rejected_without_replacing_saved_results(staged_state, corruption):
    pilot.generate_knowledge_brief(staged_state.engine, 1, StagedModel(fail_at=999))
    before, preview = _stored(staged_state.engine), _saved_preview(staged_state.engine)
    rows = _stage_rows(staged_state.engine)
    assert rows
    with Session(staged_state.engine) as db:
        for detached in rows:
            row = db.get(ProjectHistoryBatch, (1, detached.fingerprint))
            raw = '{not JSON' if corruption == 'malformed' else json.dumps({
                'sections': {name: ([{'text': 'Foreign claim.', 'classification': 'reported',
                                     'source_ids': ['foreign:project:2']}] if name == 'overview' else [])
                             for name in SECTION_NAMES}, 'project_map': {'nodes': [], 'edges': []}})
            row.evidence = [{'stage_response': raw}]
        db.commit()
    with pytest.raises(CodexError):
        pilot.generate_knowledge_brief(staged_state.engine, 1, StagedModel())
    assert _stored(staged_state.engine) == before
    assert _saved_preview(staged_state.engine) == preview


def test_indivisible_oversized_source_fails_without_altering_saved_results(staged_state, monkeypatch):
    huge = _large_candidate(1, count=1)
    huge['items'][0]['statement'] = 'A single indivisible source. ' * 20_000
    monkeypatch.setattr(pilot, 'build_candidate', lambda *a, **kw: deepcopy(huge))
    before, preview = _stored(staged_state.engine), _saved_preview(staged_state.engine)
    manager = StagedModel()
    with pytest.raises(CodexError, match='(?i)(limit|large|size|shrink)'):
        pilot.generate_knowledge_brief(staged_state.engine, 1, manager)
    assert _stored(staged_state.engine) == before
    assert _saved_preview(staged_state.engine) == preview
    assert huge['items'][0]['statement'] == 'A single indivisible source. ' * 20_000


def test_expanding_intermediate_responses_fail_finitely_without_data_loss(staged_state, monkeypatch):
    monkeypatch.setattr(pilot, 'MAX_SYNTHESIS_CHARS', 20_000)
    before, preview = _stored(staged_state.engine), _saved_preview(staged_state.engine)

    class ExpandingModel(StagedModel):
        def generate_brief(self, prompt, schema):
            if 'groups' in schema['properties']:
                return super().generate_brief(prompt, schema)
            response = json.loads(super().generate_brief(prompt, schema))
            for section, definition in schema['properties']['sections']['properties'].items():
                allowed = definition['items']['properties']['source_ids']['items']['enum']
                limit = definition['items']['properties']['text']['maxLength']
                response['sections'][section] = [
                    {'text': (f'{section} distinct point {index}. ' + 'Supported detail. ' * limit)[:limit],
                     'classification': 'reported', 'source_ids': [allowed[0]]}
                    for index in range(definition['maxItems'])]
            assert len(self.calls) <= 100, 'Nonshrinking synthesis did not terminate.'
            return json.dumps(response)

    manager = ExpandingModel()
    with pytest.raises(CodexError, match='(?i)(limit|large|size|shrink|reduce|converg|stage|bound)'):
        pilot.generate_knowledge_brief(staged_state.engine, 1, manager)
    assert len(manager.calls) <= 100
    assert _stored(staged_state.engine) == before
    assert _saved_preview(staged_state.engine) == preview


def test_successful_staged_publication_saves_summary_and_preserves_general_overview(staged_state):
    other = _stored(staged_state.engine, 2)
    preview = _saved_preview(staged_state.engine)
    before = _stored(staged_state.engine)
    result = pilot.generate_knowledge_brief(staged_state.engine, 1, StagedModel(), publish=True)
    knowledge, brief, revisions = _stored(staged_state.engine)
    assert result['published'] is True
    assert knowledge['publication_id'] == knowledge['summary']['project_state']['publication_id'] == result['publication_id']
    assert knowledge['summary'] == result['brief']
    assert brief == before[1]
    assert len(knowledge['items']) == 520
    assert revisions[:-1] == before[2] and revisions[-1][0] == 'knowledge'
    assert _stored(staged_state.engine, 2) == other
    assert _saved_preview(staged_state.engine) == preview
    diagnostic = result['diagnostics']['overview_selection']
    assert diagnostic['partial'] is False
    assert diagnostic['selected_items'] == diagnostic['available_items'] == 520
    assert diagnostic['omitted_items'] == 0
    assert diagnostic['staged_synthesis'] is True


def test_stages_keep_acceptance_proposal_pair_as_original_citations(staged_state, monkeypatch):
    candidate = deepcopy(staged_state.candidates[1])
    proposal, acceptance = candidate['sources'][:2]
    proposal.update(role='assistant', conversation_id='shared-turn', order=1)
    acceptance.update(role='user', conversation_id='shared-turn', order=2)
    first = candidate['items'][0]
    first['agreement_basis'] = 'proposal_acceptance'
    first['evidence'] = [
        {'source_id': source['id'], 'role': source['role'], 'excerpt': source['text'],
         'conversation_id': source['conversation_id'], 'order': source['order']}
        for source in (proposal, acceptance)]
    candidate['items'][1]['evidence'] = [deepcopy(first['evidence'][1])]
    monkeypatch.setattr(pilot, 'build_candidate', lambda *a, **kw: deepcopy(candidate))
    result = pilot.generate_knowledge_brief(staged_state.engine, 1, StagedModel())
    citations = result['brief']['sections']['overview'][0]['source_ids']
    assert proposal['id'] in citations and acceptance['id'] in citations
    originals = {source['id']: source for source in result['brief']['sources']}
    assert originals[proposal['id']]['role'] == 'assistant'
    assert originals[acceptance['id']]['role'] == 'user'
    assert originals[proposal['id']]['text'] == proposal['text']
    assert originals[acceptance['id']]['text'] == acceptance['text']


def test_later_context_uses_exact_excerpt_records_across_stages(staged_state, monkeypatch):
    candidate = deepcopy(staged_state.candidates[1])
    request, reply = candidate['sources'][:2]
    request.update(conversation_id='same-discussion', turn_id='turn-one', message_order=1, order=1)
    reply.update(conversation_id='same-discussion', turn_id='turn-one', message_order=2, order=2,
                 role='assistant', text='A joined display value is not the authoritative quotation.',
                 excerpts=['Implemented on the development branch.', 'Validation was reported in this reply.'])
    candidate['items'][0]['evidence'][0].update(conversation_id='same-discussion', order=1)
    candidate['items'][1]['evidence'][0].update(role='assistant', conversation_id='same-discussion',
                                              order=2, excerpt=reply['excerpts'][0])
    monkeypatch.setattr(pilot, 'build_candidate', lambda *a, **kw: deepcopy(candidate))

    class CiteContext(StagedModel):
        context_seen = False

        def generate_brief(self, prompt, schema):
            if 'groups' in schema['properties']:
                return super().generate_brief(prompt, schema)
            response = json.loads(super().generate_brief(prompt, schema))
            contexts = [source for source in self.calls[-1]['sources']
                        if source.get('kind') == 'original_conversation_context']
            if contexts:
                self.context_seen = True
                assert contexts[0]['excerpts'] == reply['excerpts']
                for section in ('overview', 'current_status', 'architecture'):
                    response['sections'][section][0]['source_ids'] = [contexts[0]['id']]
            return json.dumps(response)

    manager = CiteContext()
    result = pilot.generate_knowledge_brief(staged_state.engine, 1, manager)
    assert manager.context_seen
    assert reply['id'] in result['brief']['sections']['overview'][0]['source_ids']
    stored = next(source for source in result['brief']['sources'] if source['id'] == reply['id'])
    assert stored['excerpts'] == reply['excerpts']
