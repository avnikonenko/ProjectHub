"""Primary claim context survives synthesis encoding; semantic checks need real AI.

The model below only captures inputs and supplies routing fixtures. Its output is
not an assertion that an LLM correctly evaluates support or follows instructions.
"""
from copy import deepcopy
import json

import pytest

from backend.app.integrations.codex.brief import SECTION_NAMES
from backend.app.integrations.codex.knowledge_brief import _synthesize
from backend.app.integrations.codex.protocol import CodexError


def _reference(identifier, role, excerpt, order):
    return {'source_id': identifier, 'role': role, 'excerpt': excerpt,
            'conversation_id': 'original-discussion', 'order': order}


@pytest.fixture
def claim_context():
    selection = _reference('original-user-selection', 'user',
                           'Use https://example.org/records/741 as the first dataset.', 1)
    recommendation = _reference('original-assistant-recommendation', 'assistant',
                                'We should record the exact release label for reproducibility.', 2)
    proposal = _reference('original-assistant-scope', 'assistant',
                          'Should the project support both workflows?', 3)
    acceptance = _reference('original-user-acceptance', 'user', 'Yes.', 4)
    first_release = _reference('original-assistant-first-release', 'assistant',
                               'I propose inspection only for the first release.', 5)

    def item(identifier, statement, category, status, scope, evidence, **extra):
        return {'id': identifier, 'project_id': 1, 'statement': statement,
                'category': category, 'design_status': status, 'scope': scope,
                'source_state': 'valid', 'agreement_basis': 'none',
                'rationale': '', 'limitations': '', 'evidence': evidence, **extra}

    items = [
        item('knowledge:selected-record', 'Use the selected record as the first dataset.',
             'DECISION', 'AGREED', 'Initial dataset', [selection], agreement_basis='direct_user',
             # Deliberately polluted interpretation: primary selection must still
             # reach the model and must not be replaced by this invented caveat.
             limitations='The dataset release is unpinned.'),
        item('knowledge:release-recommendation', 'Record the exact release label.',
             'PROPOSAL', 'PROPOSED', 'Reproducibility policy', [recommendation]),
        item('knowledge:long-term-scope', 'Support both workflows.', 'DECISION', 'AGREED',
             'Long-term project scope', [proposal, acceptance], agreement_basis='proposal_acceptance'),
        item('knowledge:first-release', 'Use inspection only for the first release.',
             'PROPOSAL', 'PROPOSED', 'First release', [first_release],
             limitations='The user has not accepted this first-release boundary.'),
    ]
    refs = [selection, recommendation, proposal, acceptance, first_release]
    originals = [{'id': ref['source_id'], 'label': ref['source_id'], 'text': ref['excerpt'],
                  'role': ref['role'], 'conversation_id': ref['conversation_id'], 'order': ref['order']}
                 for ref in refs]
    candidate = {'project_id': 1, 'items': items, 'sources': originals,
                 'coverage': {'limitations': ['Only the supplied discussion was retrieved.']}}
    bundle = {'project': {'id': 1, 'name': 'Synthetic project', 'description': '', 'goals': ''}}
    return candidate, bundle


class CaptureSynthesis:
    def __init__(self, selected_indexes, foreign=False):
        self.selected_indexes, self.foreign = selected_indexes, foreign
        self.payload = None

    def generate_brief(self, prompt, schema):
        self.payload = json.loads(prompt.split('\n', 1)[1])
        enum = schema['properties']['sections']['properties']['overview']['items']['properties']['source_ids']['items']['enum']
        references = ['not-in-this-request'] if self.foreign else [enum[i] for i in self.selected_indexes]
        sections = {name: [] for name in SECTION_NAMES}
        for name in ('overview', 'architecture', 'current_status'):
            sections[name] = [{'text': 'Synthetic response used only to test citation routing.',
                               'classification': 'reported', 'source_ids': references}]
        return json.dumps({'sections': sections, 'project_map': {'nodes': [], 'edges': []}})


def _decoded_items(payload):
    items = []
    for source in payload['sources']:
        if 'item' not in source:
            continue
        item = deepcopy(source['item'])
        item['evidence'] = [
            {**payload['evidence_sources'][ref['source_ref']],
             'excerpt': payload['evidence_excerpts'][ref['excerpt_id']]}
            for ref in item['evidence']]
        items.append(item)
    return items


def test_primary_selection_and_separate_recommendation_survive_polluted_optional_field(claim_context):
    candidate, bundle = claim_context
    before = deepcopy(candidate)
    manager = CaptureSynthesis([0, 1])
    sections, _, cited = _synthesize(manager, candidate, bundle, {'coverage': []})
    selection, recommendation, *_ = _decoded_items(manager.payload)

    assert selection['design_status'] == 'AGREED'
    assert selection['scope'] == 'Initial dataset'
    assert selection['limitations'] == 'The dataset release is unpinned.'
    assert selection['evidence'] == [{
        'source_id': 'original-user-selection', 'role': 'user',
        'excerpt': 'Use https://example.org/records/741 as the first dataset.',
        'conversation_id': 'original-discussion', 'order': 1,
    }]
    assert recommendation['design_status'] == 'PROPOSED'
    assert recommendation['scope'] == 'Reproducibility policy'
    assert recommendation['evidence'][0]['role'] == 'assistant'
    assert recommendation['evidence'][0]['order'] == 2
    assert recommendation['evidence'][0]['excerpt'] == 'We should record the exact release label for reproducibility.'
    assert manager.payload['coverage'] == candidate['coverage']
    assert {source['id'] for source in cited} == {'original-user-selection', 'original-assistant-recommendation'}
    assert set(sections['overview'][0]['source_ids']) == {'original-user-selection', 'original-assistant-recommendation'}
    assert candidate == before


def test_accepted_project_scope_and_proposed_release_keep_original_acceptance_context(claim_context):
    candidate, bundle = claim_context
    manager = CaptureSynthesis([2, 3])
    sections, _, cited = _synthesize(manager, candidate, bundle, {})
    *_, scope, release = _decoded_items(manager.payload)

    assert (scope['design_status'], scope['scope'], scope['agreement_basis']) == (
        'AGREED', 'Long-term project scope', 'proposal_acceptance')
    assert [(e['source_id'], e['role'], e['order'], e['excerpt']) for e in scope['evidence']] == [
        ('original-assistant-scope', 'assistant', 3, 'Should the project support both workflows?'),
        ('original-user-acceptance', 'user', 4, 'Yes.'),
    ]
    assert (release['design_status'], release['scope']) == ('PROPOSED', 'First release')
    assert release['evidence'][0]['source_id'] == 'original-assistant-first-release'
    expected = {'original-assistant-scope', 'original-user-acceptance', 'original-assistant-first-release'}
    assert set(sections['overview'][0]['source_ids']) == expected
    assert {source['id'] for source in cited} == expected


def test_foreign_alias_cannot_replace_original_claim_evidence(claim_context):
    candidate, bundle = claim_context
    before = deepcopy(candidate)
    with pytest.raises(CodexError, match='foreign sources'):
        _synthesize(CaptureSynthesis([0], foreign=True), candidate, bundle, {})
    assert candidate == before


def test_adjacent_same_scope_claims_keep_independent_alias_bindings(claim_context):
    """Routing honors model bindings; it cannot verify their semantic correctness."""
    candidate, bundle = claim_context
    candidate['items'] = candidate['items'][:2]
    candidate['items'][0]['scope'] = candidate['items'][1]['scope'] = 'Dataset reproducibility'
    before = deepcopy(candidate)

    class SeparateClaims(CaptureSynthesis):
        def generate_brief(self, prompt, schema):
            response = json.loads(super().generate_brief(prompt, schema))
            sources = self.payload['sources']
            response['sections']['next_steps'] = [
                {'text': 'Use the selected record as the first dataset.',
                 'classification': 'reported', 'source_ids': [sources[0]['id']]},
                {'text': 'Record the exact release label.',
                 'classification': 'reported', 'source_ids': [sources[1]['id']]},
            ]
            return json.dumps(response)

    manager = SeparateClaims([0, 1])
    sections, _, cited = _synthesize(manager, candidate, bundle, {})
    selection, recommendation = sections['next_steps']
    assert selection['source_ids'] == ['original-user-selection']
    assert recommendation['source_ids'] == ['original-assistant-recommendation']
    assert all('supporting_source_ids' not in point for point in sections['next_steps'])
    originals = {source['id']: source for source in cited}
    assert originals[selection['source_ids'][0]]['text'] == candidate['sources'][0]['text']
    assert originals[recommendation['source_ids'][0]]['text'] == candidate['sources'][1]['text']
    assert candidate == before
