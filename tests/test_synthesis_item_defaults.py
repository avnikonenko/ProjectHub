"""Large synthesis inputs keep every item through explicit wire-only defaults."""
from copy import deepcopy
import json

import pytest

from backend.app.integrations.codex import knowledge_brief as module
from backend.app.integrations.codex.brief import SECTION_NAMES
from backend.app.integrations.codex.protocol import CodexError


def restore_items(sources, defaults):
    restored = deepcopy(sources)
    for source in restored:
        if 'item' in source:
            source['item'] = {**defaults, **source['item']}
    return restored


def test_item_defaults_roundtrip_preserves_overrides_empty_values_and_other_records():
    common = {'source_state': 'valid', 'agreement_basis': 'none',
              'category': 'PROPOSAL', 'design_status': 'PROPOSED'}
    sources = [
        {'id': 'one', 'item': {**common, 'statement': 'First proposal.'}},
        {'id': 'two', 'item': {**common, 'category': 'REQUIREMENT', 'design_status': 'AGREED',
                              'agreement_basis': 'direct_user', 'source_state': 'unavailable'}},
        {'id': 'three', 'item': {**common, 'category': 'FINDING', 'design_status': 'PROPOSED'}},
        {'id': 'empty', 'item': {key: '' for key in common}},
        {'id': 'null', 'item': {key: None for key in common}},
        {'id': 'code:1', **common, 'text': 'Independent code.'},
        {'id': 'project:metadata', **common, 'text': 'Project metadata.'},
    ]
    before = deepcopy(sources)
    wire = deepcopy(sources)
    defaults = module._apply_interpretation_item_defaults(wire)
    assert defaults == common
    assert wire[0]['item'] == {'statement': 'First proposal.'}
    assert wire[1]['item']['design_status'] == 'AGREED'
    assert wire[2]['item']['category'] == 'FINDING'
    assert 'design_status' not in wire[2]['item']
    assert wire[3:] == sources[3:]
    assert restore_items(wire, defaults) == sources == before


def test_missing_fields_do_not_acquire_defaults():
    sources = [
        {'id': 'one', 'item': {'source_state': 'valid', 'design_status': 'PROPOSED'}},
        {'id': 'two', 'item': {'source_state': 'valid'}},
        {'id': 'code:1', 'design_status': 'PROPOSED'},
    ]
    wire = deepcopy(sources)
    defaults = module._apply_interpretation_item_defaults(wire)
    assert defaults == {'source_state': 'valid'}
    assert restore_items(wire, defaults) == sources
    assert module._apply_interpretation_item_defaults([]) == {}


def setup_synthesis(monkeypatch, count=300, statement_chars=400):
    evidence = {'source_id': 'original:source', 'role': 'assistant',
                'conversation_id': 'original:conversation', 'order': 1, 'excerpt': 'Exact source quote.'}
    sources = [{'id': f'knowledge:{index}', 'kind': 'extracted_interpretation',
                'classification': 'reported', 'item': {
                    'statement': f'Claim {index}: ' + 'x' * statement_chars,
                    'scope': 'project', 'rationale': 'Keep this original reason.',
                    'category': 'PROPOSAL', 'design_status': 'PROPOSED',
                    'source_state': 'valid', 'agreement_basis': 'none', 'evidence': [deepcopy(evidence)],
                }} for index in range(count)]
    groups = {source['id']: [{'id': evidence['source_id'], 'role': 'assistant',
                              'text': evidence['excerpt'], 'classification': 'reported'}]
              for source in sources}
    monkeypatch.setattr(module, '_brief_inputs', lambda *args: (sources, groups))
    return sources


class CaptureSynthesis:
    def __init__(self):
        self.payload = None
        self.instructions = None

    def generate_brief(self, prompt, schema):
        self.instructions, encoded = prompt.split('\n', 1)
        self.payload = json.loads(encoded)
        assert len(encoded) <= module.MAX_SYNTHESIS_CHARS
        sections = {name: [] for name in SECTION_NAMES}
        for name in ('overview', 'architecture', 'current_status'):
            sections[name] = [{'text': 'A source-supported synthetic point.',
                               'classification': 'reported', 'source_ids': ['k1']}]
        return json.dumps({'sections': sections, 'project_map': {'nodes': [], 'edges': []}})


def test_large_payload_fits_existing_limit_without_changing_source_records(monkeypatch):
    sources = setup_synthesis(monkeypatch)
    before = deepcopy(sources)
    manager = CaptureSynthesis()
    module._synthesize(manager, {'coverage': {}}, {'project': {}}, {})
    payload = manager.payload
    assert len(payload['sources']) == 300
    defaults = payload['interpretation_item_defaults']
    assert defaults == {'source_state': 'valid', 'agreement_basis': 'none',
                        'category': 'PROPOSAL', 'design_status': 'PROPOSED'}
    for original, source in zip(sources, payload['sources']):
        item = {**defaults, **source['item']}
        item['evidence'] = [{**payload['evidence_sources'][ref['source_ref']],
                             'excerpt': payload['evidence_excerpts'][ref['excerpt_id']]}
                            for ref in item['evidence']]
        assert item == original['item']
    assert 'Explicit item values override those defaults' in manager.instructions
    assert sources == before


def test_small_payload_keeps_existing_wire_shape(monkeypatch):
    sources = setup_synthesis(monkeypatch, count=1)
    before = deepcopy(sources)
    manager = CaptureSynthesis()
    module._synthesize(manager, {'coverage': {}}, {'project': {}}, {})
    assert 'interpretation_item_defaults' not in manager.payload
    assert manager.payload['sources'][0]['item']['category'] == 'PROPOSAL'
    assert manager.payload['sources'][0]['item']['design_status'] == 'PROPOSED'
    assert sources == before


def test_still_oversized_payload_fails_without_calling_model_or_mutating_data(monkeypatch):
    sources = setup_synthesis(monkeypatch, statement_chars=700)
    before = deepcopy(sources)
    manager = CaptureSynthesis()
    with pytest.raises(CodexError, match='synthesis input limit'):
        module._synthesize(manager, {'coverage': {}}, {'project': {}}, {})
    assert manager.payload is None
    assert sources == before
