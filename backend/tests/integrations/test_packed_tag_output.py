"""Local packed-output validation; these tests do not exercise model semantics."""
from copy import deepcopy
import json

import pytest

from backend.app.integrations.codex import compact_topic_wire as wire
from backend.app.integrations.codex.protocol import CodexError


@pytest.fixture
def catalog():
    return ([{'id': f'original-record:{index}', 'statement': f'Claim {index}', 'scope': 'project'}
             for index in range(1, 4)],
            [{'id': f'canonical-topic:{index}', 'label': f'Topic {index}', 'description': 'Subject'}
             for index in range(1, 6)])


def test_packed_assignments_restore_all_ids_and_canonical_order_without_mutation(catalog):
    batch, taxonomy = catalog
    original = deepcopy(catalog)
    reply = {'assignments': [[3], [2, 3], [1, 3, 1]]}
    assert wire.parse_packed_assignments(json.dumps(reply), batch, taxonomy) == {
        'original-record:1': ['canonical-topic:1', 'canonical-topic:3'],
        'original-record:2': ['canonical-topic:3'], 'original-record:3': [],
    }
    assert catalog == original
    assert wire.assignment_payload(batch, taxonomy)['records'][0] == {'i': 1, 'statement': 'Claim 1'}


@pytest.mark.parametrize('rows', [
    [[1], [2], [3]],
    [[1, 1], [2, 1], [3, 1]],
    [[1, 1, 2, 3, 4], [2, 5, 4, 3, 2], [3, 1, 2, 3, 4]],
])
def test_untagged_rows_and_up_to_four_memberships_preserve_explicit_coverage(catalog, rows):
    batch, taxonomy = catalog
    parsed = wire.parse_packed_assignments(json.dumps({'assignments': rows}), batch, taxonomy)
    assert list(parsed) == [record['id'] for record in batch]
    assert all(len(topics) <= 4 for topics in parsed.values())


def test_empty_batch_needs_no_assignments():
    assert wire.parse_packed_assignments('{"assignments":[]}', [], []) == {}


def test_empty_taxonomy_allows_only_explicit_untagged_rows(catalog):
    batch, _ = catalog
    assert wire.parse_packed_assignments('{"assignments":[[1],[2],[3]]}', batch, []) == {
        record['id']: [] for record in batch}
    with pytest.raises(CodexError):
        wire.parse_packed_assignments('{"assignments":[[1,1],[2],[3]]}', batch, [])


@pytest.mark.parametrize('rows', [
    [],
    [[1], [3]],
    [[1], [2], [2]],
    [[1], [2], [3], [3]],
    [[1, 1, 1], [2], [3]],
    [[1, 1, 2, 3, 4, 5], [2], [3]],
    [[0], [2], [3]],
    [[4], [2], [3]],
    [[-1], [2], [3]],
    [[1, 0], [2], [3]],
    [[1, -1], [2], [3]],
    [[1, 6], [2], [3]],
    [[True], [2], [3]],
    [[1, True], [2], [3]],
    [[1.0], [2], [3]],
    [[1, 1.0], [2], [3]],
    [['1'], [2], [3]],
    [[1, '1'], [2], [3]],
    [[[1]], [2], [3]],
    [[1, [1]], [2], [3]],
    [[None], [2], [3]],
    [[1, None], [2], [3]],
    [[], [2], [3]],
    [None, [2], [3]],
    [{'i': 1, 't': []}, [2], [3]],
    ['1', [2], [3]],
])
def test_invalid_packed_rows_fail_closed(catalog, rows):
    with pytest.raises(CodexError):
        wire.parse_packed_assignments(json.dumps({'assignments': rows}), *catalog)


@pytest.mark.parametrize('reply', [
    {'assignments': None},
    {'assignments': {}},
    {},
    {'assignments': [[1], [2], [3]], 'extra': []},
    {'groups': [], 'untagged': [1, 2, 3]},
    [],
])
def test_invalid_packed_response_structure_fails_closed(catalog, reply):
    with pytest.raises(CodexError):
        wire.parse_packed_assignments(json.dumps(reply), *catalog)


@pytest.mark.parametrize('raw', [
    '{"assignments":[[1],[2],[3]],"assignments":[]}',
    '```json\n{"assignments":[[1],[2],[3]]}\n```',
    '{"assignments":[[1],[2],[3]]} trailing',
    None,
])
def test_packed_output_reuses_strict_json_parsing(catalog, raw):
    with pytest.raises(CodexError):
        wire.parse_packed_assignments(raw, *catalog)


def test_packed_output_reply_size_bound(catalog, monkeypatch):
    raw = '{"assignments":[[1],[2],[3]]}'
    monkeypatch.setattr(wire, 'MAX_REPLY_CHARS', len(raw) - 1)
    with pytest.raises(CodexError):
        wire.parse_packed_assignments(raw, *catalog)


def test_packed_schema_has_exact_fields_and_bounded_numeric_rows(catalog):
    schema = wire.packed_assignment_schema(*catalog)
    assert schema['required'] == ['assignments']
    assert schema['additionalProperties'] is False
    assert schema['properties']['assignments'] == {
        'type': 'array', 'minItems': 3, 'maxItems': 3,
        'items': {'type': 'array', 'minItems': 1, 'maxItems': 5,
                  'items': {'type': 'integer', 'minimum': 1, 'maximum': 5}},
    }


def test_packed_schema_integer_range_includes_record_ids_with_smaller_taxonomy(catalog):
    batch, taxonomy = catalog
    assert wire.packed_assignment_schema(batch, taxonomy[:1])['properties']['assignments'][
        'items']['items']['maximum'] == len(batch)
    with pytest.raises(CodexError):
        wire.parse_packed_assignments('{"assignments":[[1,2],[2],[3]]}', batch, taxonomy[:1])


def test_existing_assignment_output_contract_is_unchanged(catalog):
    batch, taxonomy = catalog
    raw = json.dumps({'assignments': [{'i': 1, 't': [2]}, {'i': 2, 't': []}, {'i': 3, 't': [1]}]})
    assert wire.parse_assignments(raw, batch, taxonomy) == {
        'original-record:1': ['canonical-topic:2'], 'original-record:2': [],
        'original-record:3': ['canonical-topic:1'],
    }
