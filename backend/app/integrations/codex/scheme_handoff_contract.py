"""Build and check graph relationships backed by supplied local code evidence."""

from hashlib import sha256
import json


_TYPES = {'value_transfer', 'conditional_sequence'}
_NODE_PREFIX = 'hn_'
_MAX_CONTRACTS = 3
_MAX_PAYLOAD_CHARS = 2200
_MAX_SOURCE_RECORDS = 64


def _text(value, limit):
    return isinstance(value, str) and 0 < len(value) <= limit and value.isprintable()


def _endpoint(source_id, revision, symbol, line, column):
    if (not _text(symbol, 100) or not all(part.isidentifier() for part in symbol.split('.'))
            or type(line) is not int or not 0 < line <= 200_000
            or type(column) is not int or not 0 <= column <= 200_000):
        return None
    digest = sha256(json.dumps([source_id, revision, line, column, symbol]).encode()).hexdigest()[:24]
    return {'id': _NODE_PREFIX + digest, 'label': symbol, 'line': line, 'column': column}


def make_contracts(sources):
    """Use only structured handoffs in the final, budgeted evidence sources."""
    contracts, seen, targets = [], set(), set()
    for source in sources:
        source_id, handoffs = source.get('id'), source.get('local_handoffs')
        if not _text(source_id, 200) or not isinstance(handoffs, list):
            continue
        revision = source.get('revision', source.get('content_hash', ''))
        if not isinstance(revision, str) or len(revision) > 128:
            continue
        for handoff in handoffs[:_MAX_SOURCE_RECORDS]:
            if not isinstance(handoff, dict):
                continue
            relationship, context = handoff.get('type'), handoff.get('context')
            if not isinstance(relationship, str) or relationship not in _TYPES or not _text(context, 400):
                continue
            origin = _endpoint(source_id, revision, handoff.get('from_symbol'), handoff.get('from_line'),
                               handoff.get('from_column', 0))
            destination = _endpoint(source_id, revision, handoff.get('to_symbol'), handoff.get('to_line'),
                                    handoff.get('to_column', 0))
            if not origin or not destination or origin == destination:
                continue
            identity = (origin['id'], destination['id'], relationship)
            # Repeated operands feeding the same operation would otherwise
            # consume the entire verification allowance.
            target_key = destination['id'], relationship, origin['label']
            if identity in seen or target_key in targets:
                continue
            key = 'hc_' + sha256(json.dumps(identity).encode()).hexdigest()[:24]
            contract = {'key': key, 'source_id': source_id, 'type': relationship,
                        'source_node': origin, 'target_node': destination, 'context': context}
            if len(json.dumps([*contracts, contract], ensure_ascii=False)) > _MAX_PAYLOAD_CHARS:
                continue
            seen.add(identity)
            targets.add(target_key)
            contracts.append(contract)
            if len(contracts) == _MAX_CONTRACTS:
                return contracts
    return contracts


def _matches_node(actual, expected, source_id):
    return (actual is not None and actual['label'] == expected['label']
            and actual['status'] == 'observed' and source_id in actual['source_ids'])


def check_contracts(graph, contracts):
    """Check reserved edges in a structurally validated graph against exact evidence."""
    expected = {(contract['source_node']['id'], contract['target_node']['id'], contract['type']): contract
                for contract in contracts}
    nodes = {node['id']: node for node in graph['nodes']}
    missing = {contract['key'] for contract in contracts}
    invalid = set()
    for edge in graph['edges']:
        identity = edge['source'], edge['target'], edge['type']
        origin, destination, relationship = identity
        contract = expected.get(identity)
        if contract is None:
            if relationship in _TYPES or (origin.startswith(_NODE_PREFIX) and destination.startswith(_NODE_PREFIX)):
                invalid.add(identity)
            continue
        source_id = contract['source_id']
        if (edge['status'] == 'observed' and source_id in edge['source_ids']
                and _matches_node(nodes.get(origin), contract['source_node'], source_id)
                and _matches_node(nodes.get(destination), contract['target_node'], source_id)):
            missing.discard(contract['key'])
        else:
            invalid.add(identity)
    return missing, invalid
