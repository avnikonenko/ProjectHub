from copy import deepcopy
import json

import pytest

from backend.app.integrations.codex.overview_selection import select_overview_sources
from backend.app.integrations.codex.protocol import CodexError
from backend.app.integrations.codex import knowledge_brief as brief
from tests.test_synthesis_capacity import source_views, Capture


def test_large_overview_stages_every_record_without_changing_knowledge(monkeypatch):
    sources = source_views(monkeypatch, count=3009, statement_chars=420)
    before = deepcopy(sources)
    class AllStages(Capture):
        def __init__(self):
            super().__init__()
            self.sizes = []
            self.seen = set()

        def generate_brief(self, prompt, schema):
            result = super().generate_brief(prompt, schema)
            self.sizes.append(self.encoded_size)
            self.seen.update(source['item']['statement'].split(':', 1)[0]
                             for source in self.payload['sources']
                             if source.get('item', {}).get('statement', '').startswith('Independent claim '))
            return result

    capture, selection = AllStages(), {}
    brief._synthesize(capture, {'coverage': {}}, {'project': {}}, {}, selection_diagnostics=selection)
    assert capture.encoded_size <= brief.MAX_SYNTHESIS_CHARS
    assert all(size <= brief.MAX_SYNTHESIS_CHARS for size in capture.sizes)
    assert selection['partial'] is False
    assert selection['selected_items'] == selection['available_items'] == 3009
    assert selection['omitted_items'] == 0
    assert selection['input_chars'] == max(capture.sizes)
    assert capture.seen == {f'Independent claim {i}' for i in range(3009)}
    assert sources == before


def test_conflicts_are_atomic_and_scopes_and_statuses_remain_independent():
    sources = [{'id': str(i), 'item': {'statement': 'x' * 100, 'scope': str(i % 3),
                'category': 'REQUIREMENT' if i % 2 else 'PROPOSAL',
                'design_status': 'AGREED' if i % 2 else 'PROPOSED'}} for i in range(20)]
    sources[0]['item']['conflicts_with'] = ['1']
    sources[1]['item']['conflicts_with'] = ['2']
    measure = lambda value: len(json.dumps(value))
    result, info = select_overview_sources(sources, measure, 1500)
    ids = {source['id'] for source in result}
    assert not ids.intersection({'0', '1', '2'}) or {'0', '1', '2'} <= ids
    assert {source['item']['design_status'] for source in result} == {'AGREED', 'PROPOSED'}
    assert len({source['item']['scope'] for source in result}) > 1
    assert (result, info) == select_overview_sources(sources, measure, 1500)


def test_indivisible_sources_fail_without_truncation():
    fixed = [{'id': 'code:1', 'text': 'x' * 5000}]
    with pytest.raises(CodexError, match='metadata and repository'):
        select_overview_sources(fixed, lambda value: len(json.dumps(value)), 1000)
    huge = [{'id': 'a', 'item': {'statement': 'x' * 5000}}]
    with pytest.raises(CodexError, match='No complete knowledge'):
        select_overview_sources(huge, lambda value: len(json.dumps(value)), 1000)


def test_budget_measurement_uses_the_same_source_order_as_final_encoding():
    sources = [
        {'id': 'proposed', 'item': {'statement': 'Proposal', 'design_status': 'PROPOSED'}},
        {'id': 'metadata', 'text': 'Project objective'},
        {'id': 'agreed', 'item': {'statement': 'Accepted choice', 'design_status': 'AGREED'}},
    ]
    calls = []
    def measure(values):
        ids = [source['id'] for source in values]
        assert ids == [source['id'] for source in sources if source['id'] in set(ids)]
        calls.append(ids)
        return len(json.dumps(values))
    selected, _ = select_overview_sources(sources, measure, 1000)
    assert selected == sources and len(calls) > 1


def test_accepted_requirements_outweigh_reported_noise_without_erasing_diversity():
    def record(identifier, status, category, scope):
        return {'id': identifier, 'item': {'statement': 'A complete supported statement.',
                'design_status': status, 'category': category, 'scope': scope,
                'rationale': 'Preserve the original reason.', 'evidence': [{'excerpt': 'Exact evidence.'}]}}
    sources = [record(f'noise:{i}', 'UNKNOWN', 'FINDING', f'context {i}') for i in range(500)]
    sources += [record(f'constraint:{i}', 'AGREED', 'REQUIREMENT', f'policy {i}') for i in range(100)]
    sources += [record(f'decision:{i}', 'AGREED', 'DECISION', f'component {i}') for i in range(30)]
    sources += [record(f'question:{i}', 'UNRESOLVED', 'OPEN_QUESTION', f'question {i}') for i in range(30)]
    sources += [record(f'proposal:{i}', 'PROPOSED', 'PROPOSAL', f'proposal {i}') for i in range(30)]
    before = deepcopy(sources)
    result, info = select_overview_sources(sources, len, 68)
    counts = info['selected_by_status']
    assert counts['AGREED'] >= 45
    assert 0 < counts['UNKNOWN'] < 10
    assert counts['UNRESOLVED'] >= 6
    assert counts['PROPOSED'] >= 3
    assert info['selected_by_category']['REQUIREMENT'] >= 30
    assert info['available_by_status']['UNKNOWN'] == 500
    assert len(result) == 68 and sources == before


def test_sparse_scopes_get_a_turn_before_prolific_scope_repeats():
    sources = [{'id': f'large:{i}', 'item': {'statement': 'Constraint', 'category': 'REQUIREMENT',
                'design_status': 'AGREED', 'scope': 'A large scope'}} for i in range(100)]
    sources += [{'id': f'sparse:{i}', 'item': {'statement': 'Another constraint', 'category': 'REQUIREMENT',
                 'design_status': 'AGREED', 'scope': f'Z sparse scope {i}'}} for i in range(4)]
    result, _ = select_overview_sources(sources, len, 4)
    assert {s['id'] for s in result} == {f'sparse:{i}' for i in range(4)}


def test_equal_scope_ties_are_stable_hash_order_not_alphabetical():
    from hashlib import sha256
    sources = [{'id': str(i), 'item': {'statement': 'Constraint', 'category': 'REQUIREMENT',
                'design_status': 'AGREED', 'scope': f'Scope {i:02}'}} for i in range(20)]
    expected = sorted(sources, key=lambda s: sha256(s['item']['scope'].encode()).digest())[:5]
    selected, _ = select_overview_sources(sources, len, 5)
    assert {s['id'] for s in selected} == {s['id'] for s in expected}
    assert {s['id'] for s in selected} != {str(i) for i in range(5)}


def test_required_later_context_is_atomic_with_its_knowledge_item():
    sources = [
        {'id': 'question', 'item': {'statement': 'Historical question', 'design_status': 'UNRESOLVED'}},
        {'id': 'other', 'item': {'statement': 'Independent context'}},
    ]
    companions = {'question': [{'id': 'context:answer', 'text': 'Later answer'}]}
    selected, info = select_overview_sources(sources, len, 1, companions=companions)
    assert [s['id'] for s in selected] == ['other']
    assert info['selected_items'] == 1 and info['selected_context_sources'] == 0
    assert info['omitted_context_required_units'] == 1
    selected, info = select_overview_sources(sources[:1], len, 2, companions=companions)
    assert [s['id'] for s in selected] == ['question', 'context:answer']
    assert info['selected_items'] == 1 and info['selected_context_sources'] == 1
    assert info['policy'] == 'accepted_requirements_with_later_context_v3'
    with pytest.raises(CodexError, match='No complete knowledge'):
        select_overview_sources(sources[:1], len, 1, companions=companions)


def test_shared_context_is_deduplicated_without_recursive_expansion_or_mutation():
    sources = [{'id': str(i), 'item': {'statement': 'Claim', 'design_status': 'AGREED'}} for i in range(3)]
    answer = {'id': 'context:answer', 'text': 'Final reported answer'}
    companions = {str(i): [answer] for i in range(3)}
    companions['context:answer'] = [{'id': 'context:unused', 'text': 'Not recursively required'}]
    before = deepcopy((sources, companions))
    selected, info = select_overview_sources(sources, len, 4, companions=companions)
    assert selected == [*sources, answer]
    assert info['selected_items'] == 3 and info['selected_context_sources'] == 1
    assert (sources, companions) == before


def test_relationship_unit_requires_context_for_every_member():
    sources = [
        {'id': 'a', 'item': {'statement': 'First', 'conflicts_with': ['b']}},
        {'id': 'b', 'item': {'statement': 'Second'}},
        {'id': 'c', 'item': {'statement': 'Independent'}},
    ]
    companions = {'b': [{'id': 'context:answer', 'text': 'Later related report'}]}
    selected, _ = select_overview_sources(sources, len, 2, companions=companions)
    assert [s['id'] for s in selected] == ['c']
    selected, _ = select_overview_sources(sources[:2], len, 3, companions=companions)
    assert [s['id'] for s in selected] == ['a', 'b', 'context:answer']


def test_context_cost_measurement_matches_final_base_then_context_order():
    sources = [
        {'id': 'b', 'item': {'statement': 'Second'}},
        {'id': 'metadata', 'text': 'Metadata'},
        {'id': 'a', 'item': {'statement': 'First', 'design_status': 'AGREED'}},
    ]
    companions = {'a': [{'id': 'context:z', 'text': 'Z'}, {'id': 'context:a', 'text': 'A'}]}
    def measure(values):
        ids = [s['id'] for s in values]
        base_ids = [s['id'] for s in sources if s['id'] in ids]
        assert ids == base_ids + sorted(set(ids) - set(base_ids))
        return len(values)
    selected, info = select_overview_sources(sources, measure, 5, companions=companions)
    assert [s['id'] for s in selected] == ['b', 'metadata', 'a', 'context:a', 'context:z']
    assert (selected, info) == select_overview_sources(sources, measure, 5, companions=companions)


def test_conflicting_context_identity_is_rejected():
    source = {'id': 'item', 'item': {'statement': 'Claim'}}
    with pytest.raises(CodexError, match='conflicting source identities'):
        select_overview_sources([source], len, 5, companions={'item': [source]})
    with pytest.raises(CodexError, match='conflicting source identities'):
        select_overview_sources([source], len, 5, companions={
            'item': [{'id': 'context:1', 'text': 'One'}, {'id': 'context:1', 'text': 'Different'}]})
