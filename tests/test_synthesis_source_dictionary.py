"""Synthesis evidence dictionaries preserve every source field and quotation."""
from copy import deepcopy
import json

import pytest

from backend.app.integrations.codex import knowledge_brief as module
from backend.app.integrations.codex.brief import SECTION_NAMES
from backend.app.integrations.codex.protocol import CodexError
from backend.app.integrations.codex.knowledge_brief import (
    _compact_evidence, _alias_source_ids, _apply_interpretation_defaults,
)


def _restore(wire, quotes, evidence_sources):
    restored = deepcopy(wire)
    for source in restored:
        for evidence in source.get('item', {}).get('evidence', []):
            metadata = deepcopy(evidence_sources[evidence['source_ref']])
            metadata['excerpt'] = quotes[evidence['excerpt_id']]
            evidence.clear()
            evidence.update(metadata)
    return restored


def test_wire_dictionary_roundtrip_preserves_all_fields_and_original_views():
    metadata = {'source_id': 'conversation:original:user:2', 'role': 'user',
                'conversation_id': 'conversation:original', 'order': 2}
    sources = [
        {'id': 'knowledge:1', 'classification': 'reported', 'item': {
            'statement': 'Use the agreed approach.', 'scope': 'project', 'design_status': 'AGREED',
            'agreement_basis': 'proposal_acceptance', 'rationale': 'Preserve the reason.',
            'limitations': 'A retained limitation.', 'conflicts_with': ['knowledge:3'],
            'evidence': [
                {**metadata, 'role': 'assistant', 'order': 1, 'excerpt': 'Should we use this approach?'},
                {**metadata, 'excerpt': 'Yes.'},
            ],
        }},
        {'id': 'knowledge:2', 'item': {'statement': 'A separate scope.', 'scope': 'v1', 'evidence': [
            {**metadata, 'excerpt': 'Yes.'}, {**metadata, 'excerpt': 'Preserve exact Unicode: Zn²⁺.'},
        ]}},
        {'id': 'code:1', 'text': 'Independent code evidence.', 'classification': 'inferred'},
    ]
    before = deepcopy(sources)
    wire, quotes, dictionary = _compact_evidence(sources)
    assert _restore(wire, quotes, dictionary) == sources == before
    assert len(dictionary) == 2  # Same ID with different role/order remains distinct.
    assert len(quotes) == 3
    assert all(set(ref) == {'source_ref', 'excerpt_id'} for source in wire
               for ref in source.get('item', {}).get('evidence', []))
    wire[0]['item']['statement'] = 'wire-only edit'
    dictionary['s1']['role'] = 'wire-only edit'
    assert sources == before


def test_shared_metadata_reduces_size_without_dropping_any_knowledge():
    sources = [{'id': f'knowledge:{index}', 'item': {
        'statement': f'Independent requirement {index}.', 'rationale': 'Keep this reason.',
        'evidence': [{'source_id': 'chatgpt:note:1:message:2:fragment:1', 'role': 'assistant',
                      'conversation_id': 'chatgpt:note:1', 'order': 2, 'excerpt': f'Exact quote {ref}.'}
                     for ref in range(6)],
    }} for index in range(100)]
    wire, quotes, dictionary = _compact_evidence(sources)
    assert _restore(wire, quotes, dictionary) == sources
    assert len(dictionary) == 1
    assert len(quotes) == 6
    compact = json.dumps({'sources': wire, 'evidence_sources': dictionary, 'evidence_excerpts': quotes},
                         separators=(',', ':'))
    # Compare with the previous quote-only encoding, using identical separators.
    previous = deepcopy(sources)
    quote_ids = {quote: identifier for identifier, quote in quotes.items()}
    for source in previous:
        for evidence in source['item']['evidence']:
            evidence['excerpt_id'] = quote_ids[evidence.pop('excerpt')]
    old = json.dumps({'sources': previous, 'evidence_excerpts': quotes}, separators=(',', ':'))
    assert len(compact) < len(old) - 30_000


def test_dictionary_identity_preserves_missing_versus_null_metadata():
    sources = [{'id': 'knowledge:1', 'item': {'evidence': [
        {'source_id': 'same', 'role': 'user', 'excerpt': 'Exact.'},
        {'source_id': 'same', 'role': 'user', 'conversation_id': None, 'excerpt': 'Exact.'},
        {'source_id': 'same', 'role': 'user', 'conversation_id': 'other', 'excerpt': 'Exact.'},
    ]}}]
    wire, quotes, dictionary = _compact_evidence(sources)
    assert len(dictionary) == 3
    assert _restore(wire, quotes, dictionary) == sources


def test_aliases_preserve_recorded_relationships_without_changing_originals():
    sources = [{'id': 'knowledge:long-identity-one', 'item': {
        'conflicts_with': ['knowledge:long-identity-two', 'historical-not-included'],
        'relationships': [{'target': 'knowledge:long-identity-two', 'kind': 'conflict'}],
        'evidence': [],
    }}, {'id': 'knowledge:long-identity-two', 'item': {'evidence': []}}]
    original = deepcopy(sources)
    wire, _, _ = _compact_evidence(sources)
    aliases = _alias_source_ids(wire)
    assert aliases == {'k1': 'knowledge:long-identity-one', 'k2': 'knowledge:long-identity-two'}
    assert wire[0]['item']['conflicts_with'] == ['k2', 'historical-not-included']
    assert wire[0]['item']['relationships'][0]['target'] == 'k2'
    assert sources == original


def test_interpretation_defaults_preserve_every_field_and_leave_code_separate():
    sources = [
        {'id': 'knowledge:one', 'kind': 'extracted_interpretation', 'classification': 'reported',
         'item': {'design_status': 'AGREED', 'source_state': 'valid', 'agreement_basis': 'direct_user', 'evidence': []}},
        {'id': 'knowledge:two', 'kind': 'extracted_interpretation', 'classification': 'inferred',
         'item': {'design_status': 'UNKNOWN', 'source_state': 'valid', 'evidence': []}},
        {'id': 'code:1', 'kind': 'inspected_repository', 'classification': 'inferred', 'text': 'code'},
        {'id': 'project:metadata', 'classification': 'reported', 'text': 'metadata'},
    ]
    before = deepcopy(sources)
    wire, _, _ = _compact_evidence(sources)
    defaults = _apply_interpretation_defaults(wire)
    assert defaults == {'kind': 'extracted_interpretation', 'classification': 'reported'}
    assert 'kind' not in wire[0] and 'classification' not in wire[0]
    assert wire[1]['classification'] == 'inferred'
    for source in wire:
        if 'item' in source:
            for key, value in defaults.items():
                source.setdefault(key, value)
    assert wire == before == sources


@pytest.mark.parametrize('foreign', [False, True])
def test_synthesis_aliases_are_validated_and_never_escape_as_public_citations(monkeypatch, foreign):
    refs = [
        {'source_id': 'original-proposal', 'role': 'assistant', 'conversation_id': 'same',
         'order': 1, 'excerpt': 'Use this approach?'},
        {'source_id': 'original-acceptance', 'role': 'user', 'conversation_id': 'same',
         'order': 2, 'excerpt': 'Yes.'},
    ]
    sources = [{'id': 'knowledge:durable-canonical-id', 'kind': 'extracted_interpretation',
                'classification': 'reported', 'item': {'agreement_basis': 'proposal_acceptance', 'evidence': refs}}]
    groups = {'knowledge:durable-canonical-id': [
        {'id': ref['source_id'], 'role': ref['role'], 'text': ref['excerpt']} for ref in refs]}
    original = deepcopy(sources)
    monkeypatch.setattr(module, '_brief_inputs', lambda *args: (sources, groups))

    class Synthesis:
        def generate_brief(self, prompt, schema):
            enum = schema['properties']['sections']['properties']['overview']['items']['properties']['source_ids']['items']['enum']
            assert enum == ['k1']
            reference = 'foreign:k1' if foreign else 'k1'
            sections = {name: [] for name in SECTION_NAMES}
            for name in ('overview', 'architecture', 'current_status'):
                sections[name] = [{'text': 'A supported choice.', 'classification': 'reported', 'source_ids': [reference]}]
            graph = {'nodes': [{'id': 'part', 'label': 'Component', 'source_ids': [reference]}], 'edges': []}
            return json.dumps({'sections': sections, 'project_map': graph})

    if foreign:
        with pytest.raises(CodexError, match='foreign sources'):
            module._synthesize(Synthesis(), {'coverage': {}}, {'project': {}}, {})
    else:
        sections, graph, cited = module._synthesize(Synthesis(), {'coverage': {}}, {'project': {}}, {})
        for entries in (*sections.values(), *graph.values()):
            for entry in entries:
                assert set(entry['source_ids']) == {'original-proposal', 'original-acceptance'}
                assert not any(ref.startswith('k') for ref in entry['source_ids'])
        assert {source['id'] for source in cited} == {'original-proposal', 'original-acceptance'}
    assert sources == original
