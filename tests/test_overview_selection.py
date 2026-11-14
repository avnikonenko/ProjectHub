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
