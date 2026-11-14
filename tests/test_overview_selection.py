from copy import deepcopy
import json

import pytest

from backend.app.integrations.codex.overview_selection import select_overview_sources
from backend.app.integrations.codex.protocol import CodexError
from backend.app.integrations.codex import knowledge_brief as brief
from tests.test_synthesis_capacity import source_views, Capture


def test_large_overview_bounds_actual_payload_without_changing_knowledge(monkeypatch):
    sources = source_views(monkeypatch, count=3009, statement_chars=420)
    before = deepcopy(sources)
    capture, selection = Capture(), {}
    brief._synthesize(capture, {'coverage': {}}, {'project': {}}, {}, selection_diagnostics=selection)
    assert capture.encoded_size <= brief.MAX_SYNTHESIS_CHARS
    assert selection['partial'] and selection['available_items'] == 3009
    assert selection['selected_items'] + selection['omitted_items'] == 3009
    assert selection['input_chars'] == capture.encoded_size
    assert len(selection['selected_ids']) == selection['selected_items']
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
