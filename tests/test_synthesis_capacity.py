"""The measured larger-project budget keeps plain, complete synthesis records."""
from copy import deepcopy
import json

import pytest

from backend.app.integrations.codex import knowledge_brief as module
from backend.app.integrations.codex.brief import SECTION_NAMES
from backend.app.integrations.codex.protocol import CodexError


def source_views(monkeypatch, count=339, statement_chars=420):
    evidence = {'source_id': 'original:source', 'role': 'assistant',
                'conversation_id': 'original:discussion', 'order': 1, 'excerpt': 'An exact original proposal.'}
    sources = [{'id': f'knowledge:{index}', 'kind': 'extracted_interpretation',
                'classification': 'reported', 'item': {
                    'statement': f'Independent claim {index}: ' + 'x' * statement_chars,
                    'scope': f'component {index % 7}', 'rationale': 'Retain this original rationale.',
                    'category': 'REQUIREMENT' if index == 0 else 'PROPOSAL',
                    'design_status': 'UNKNOWN' if index == 0 else 'PROPOSED',
                    'source_state': 'valid', 'agreement_basis': 'none',
                    'evidence': [deepcopy(evidence)],
                }} for index in range(count)]
    groups = {source['id']: [{'id': evidence['source_id'], 'role': 'assistant',
                              'text': evidence['excerpt'], 'classification': 'reported'}]
              for source in sources}
    monkeypatch.setattr(module, '_brief_inputs', lambda *args: (sources, groups))
    return sources


class Capture:
    def __init__(self):
        self.payload = None
        self.encoded_size = None

    def generate_brief(self, prompt, schema):
        encoded = prompt.split('\n', 1)[1]
        self.encoded_size, self.payload = len(encoded), json.loads(encoded)
        sections = {name: [] for name in SECTION_NAMES}
        for name in ('overview', 'current_status', 'architecture'):
            sections[name] = [{'text': 'A synthetic citation-routing point.', 'classification': 'reported',
                               'source_ids': ['k1']}]
        return json.dumps({'sections': sections, 'project_map': {'nodes': [], 'edges': []}})


def test_larger_complete_project_fits_without_new_codecs_or_losing_fields(monkeypatch):
    sources = source_views(monkeypatch)
    before = deepcopy(sources)
    manager = Capture()
    module._synthesize(manager, {'coverage': {}}, {'project': {}}, {})
    payload = manager.payload
    assert module.MAX_SYNTHESIS_CHARS == 240_000
    assert module.SYNTHESIS_COMPACTION_CHARS == 180_000
    assert 180_000 < manager.encoded_size <= module.MAX_SYNTHESIS_CHARS
    assert len(payload['sources']) == 339
    defaults = payload['interpretation_item_defaults']
    for original, wire in zip(sources, payload['sources']):
        assert isinstance(wire, dict) and set(wire) == {'id', 'item'}
        assert isinstance(wire['item'], dict)
        item = {**defaults, **wire['item']}
        item['evidence'] = [{**payload['evidence_sources'][ref['source_ref']],
                             'excerpt': payload['evidence_excerpts'][ref['excerpt_id']]}
                            for ref in item['evidence']]
        assert item == original['item']
    assert sources == before


def test_compaction_still_starts_at_old_threshold_even_below_new_maximum(monkeypatch):
    source_views(monkeypatch, count=300, statement_chars=400)
    manager = Capture()
    module._synthesize(manager, {'coverage': {}}, {'project': {}}, {})
    payload = deepcopy(manager.payload)
    defaults = payload.pop('interpretation_item_defaults')
    assert defaults
    for source in payload['sources']:
        source['item'] = {**defaults, **source['item']}
    uncompressed = len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
    assert module.SYNTHESIS_COMPACTION_CHARS < uncompressed < module.MAX_SYNTHESIS_CHARS
    assert manager.encoded_size < uncompressed


def test_project_above_new_bound_still_fails_before_model_call(monkeypatch):
    sources = source_views(monkeypatch, count=600, statement_chars=450)
    sources.append({'id': 'code:1', 'kind': 'inspected_repository', 'text': 'PRIVATE_CODE_MARKER'})
    before = deepcopy(sources)
    sizes = []
    encode = json.dumps

    def measure(value, *args, **kwargs):
        serialized = encode(value, *args, **kwargs)
        if isinstance(value, dict) and 'project' in value and 'sources' in value:
            sizes.append(len(serialized))
        return serialized

    monkeypatch.setattr(module.json, 'dumps', measure)
    manager = Capture()
    with pytest.raises(CodexError, match='synthesis input limit') as failure:
        module._synthesize(manager, {'coverage': {}}, {'project': {'name': 'PRIVATE_PROJECT_MARKER'}}, {})
    message = str(failure.value)
    assert f'{sizes[-1]:,} characters' in message
    assert f'limit {module.MAX_SYNTHESIS_CHARS:,}' in message
    assert f'{sizes[-1] - module.MAX_SYNTHESIS_CHARS:,} over' in message
    assert sizes[-1] < sizes[0]  # Report the actual compacted size, not the raw input.
    assert '600 knowledge items and 1 repository excerpt' in message
    assert 'time budget or reading threads will not fix' in message
    assert 'Saved Knowledge and Overview are unchanged' in message
    assert 'reading batches remain cached' in message
    assert 'PRIVATE_' not in message
    assert manager.payload is None
    assert sources == before
