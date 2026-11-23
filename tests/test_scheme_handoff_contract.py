"""Endpoint and relationship checks for evidence-backed Scheme handoffs."""

from copy import deepcopy
import json

import pytest

from backend.app.integrations.codex.scheme_handoff_contract import check_contracts, make_contracts


def _fact(from_symbol='prepare', from_line=8, to_symbol='store.write', to_line=12, kind='value_transfer'):
    return {'type': kind, 'from_symbol': from_symbol, 'from_line': from_line,
            'to_symbol': to_symbol, 'to_line': to_line, 'context': 'result passed directly under enabled'}


def _source(*facts):
    return {'id': 'repo:input', 'local_handoffs': list(facts)}


def _graph(contract):
    origin, destination = contract['source_node'], contract['target_node']
    refs = [contract['source_id']]
    return {
        'nodes': [{'id': node['id'], 'label': node['label'], 'status': 'observed', 'source_ids': refs.copy()}
                  for node in (origin, destination)],
        'edges': [{'source': origin['id'], 'target': destination['id'], 'type': contract['type'],
                   'status': 'observed', 'source_ids': refs.copy()}],
    }


def _edge_key(edge):
    return edge['source'], edge['target'], edge['type']


def test_identity_is_stable_across_batch_composition_and_rejects_duplicate_facts():
    fact = _fact()
    [first] = make_contracts([_source(fact, deepcopy(fact))])
    assert make_contracts([{'id': 'other'}, _source(fact)]) == [first]
    second = make_contracts([_source(_fact(to_line=13))])[0]
    assert second['key'] != first['key']
    assert second['source_node']['id'] == first['source_node']['id']
    assert second['target_node']['id'] != first['target_node']['id']


def test_contracts_skip_unusable_endpoints_and_bound_the_prompt_payload():
    unusable = [_fact(from_line=True), _fact(from_symbol='store.<call>'), _fact(kind='unknown'),
                _fact(from_symbol='store.write', from_line=12),
                {**_fact(), 'context': 'keep\x00out'}]
    long = [{**_fact(from_line=i), 'context': 'x' * 400} for i in range(15, 24)]
    evidence = [_source(*unusable, *long)]
    checked = make_contracts(evidence)
    assert checked
    assert len(checked) <= 3
    assert len(json.dumps(checked, ensure_ascii=False)) <= 2200
    assert all(contract['source_node']['line'] >= 15 for contract in checked)


def test_matching_named_endpoints_and_edge_confirm_presence():
    [contract] = make_contracts([_source(_fact())])
    assert check_contracts(_graph(contract), [contract]) == (set(), set())


@pytest.mark.parametrize('change', ['swapped', 'wrong-type', 'wrong-label', 'wrong-node-status',
                                    'wrong-status', 'edge-citation', 'node-citation'])
def test_citations_or_copied_node_ids_alone_do_not_verify_relation(change):
    [contract] = make_contracts([_source(_fact())])
    graph = _graph(contract)
    edge = graph['edges'][0]
    if change == 'swapped':
        edge['source'], edge['target'] = edge['target'], edge['source']
    elif change == 'wrong-type':
        edge['type'] = 'conditional_sequence'
    elif change == 'wrong-label':
        graph['nodes'][0]['label'] = 'different_producer'
    elif change == 'wrong-node-status':
        graph['nodes'][0]['status'] = 'uncertain'
    elif change == 'wrong-status':
        edge['status'] = 'proposed'
    elif change == 'edge-citation':
        edge['source_ids'] = ['other']
    else:
        graph['nodes'][1]['source_ids'] = ['other']
    assert check_contracts(graph, [contract]) == ({contract['key']}, {_edge_key(edge)})


def test_absent_verified_edge_does_not_reject_ordinary_graph_relationship():
    [contract] = make_contracts([_source(_fact())])
    graph = _graph(contract)
    graph['nodes'].append({'id': 'general_step', 'label': 'General step',
                           'status': 'observed', 'source_ids': ['repo:input']})
    graph['edges'][0].update(source='general_step', type='precedes')
    assert check_contracts(graph, [contract]) == ({contract['key']}, set())
