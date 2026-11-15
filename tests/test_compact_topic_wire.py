import json

import pytest

from backend.app.integrations.codex import compact_topic_wire as wire
from backend.app.integrations.codex.protocol import CodexError


def record(identifier, *, scope='Project', category='Requirement', statement=None):
    return {'id': identifier, 'scope': scope, 'category': category,
            'statement': statement or f'Statement {identifier}', 'rationale': 'not sent'}


def test_taxonomy_sample_covers_categories_and_scopes_under_limits():
    records = [record(str(i), scope='Main') for i in range(80)] + [
        record('secondary', scope='Secondary'),
        record('test', category='Verification'),
        record('too-long', category='Long', statement='x' * 24_000),
    ]
    sample = wire.bootstrap_payload(records)
    assert sample == wire.bootstrap_payload(records)
    assert len(sample['sample']) == wire.SAMPLE_LIMIT
    assert len(json.dumps(sample, separators=(',', ':'))) <= wire.SAMPLE_CHARS
    assert {'Statement secondary', 'Statement test'} <= {entry['statement'] for entry in sample['sample']}
    assert all('rationale' not in entry and 'id' not in entry for entry in sample['sample'])


def test_canonical_labels_and_local_assignments():
    batch = [record('stored-1', scope='Configuration'), record('stored-2')]
    raw = json.dumps({'topics': [{'label': 'Quality Checks', 'description': 'Validation and tests.'},
                                  {'label': 'Setup', 'description': 'Configuration.'}]})
    taxonomy = wire.parse_bootstrap(raw)
    alternate = wire.parse_bootstrap(raw.replace('Quality Checks', 'quality   checks'))
    assert taxonomy[0]['id'] == alternate[0]['id']
    payload = wire.assignment_payload(batch, taxonomy)
    assert payload['records'] == [{'i': 1, 'statement': 'Statement stored-1', 'scope': 'Configuration'},
                                  {'i': 2, 'statement': 'Statement stored-2'}]
    assert payload['topics'][0] == {'i': 1, 'label': 'Quality Checks', 'description': 'Validation and tests.'}
    assert wire.parse_assignments('{"assignments":[{"i":2,"t":[]},{"i":1,"t":[2,1]}]}', batch, taxonomy) == {
        'stored-2': [], 'stored-1': [taxonomy[1]['id'], taxonomy[0]['id']]}
    assert wire.assignment_schema(batch, taxonomy)['properties']['assignments']['minItems'] == len(batch)


@pytest.mark.parametrize('raw', [
    '{"assignments":[{"i":1,"t":[1]}]}',
    '{"assignments":[{"i":1,"t":[1]},{"i":1,"t":[]}]}',
    '{"assignments":[{"i":1,"t":[2]},{"i":2,"t":[]}]}',
    '{"assignments":[{"i":true,"t":[1]},{"i":2,"t":[]}]}',
    '{"assignments":[{"i":1,"t":[1,1]},{"i":2,"t":[]}]}',
    '{"assignments":[{"i":1,"i":1,"t":[1]},{"i":2,"t":[]}]}',
    '{"assignments":[{"i":1,"t":[1]},{"i":2,"t":[true]}]}',
])
def test_assignments_reject_incomplete_or_invalid_record_and_topic_ids(raw):
    with pytest.raises(CodexError):
        wire.parse_assignments(raw, [record('a'), record('b')], [{'id': 'topic:a'}])


@pytest.mark.parametrize('labels', [['All'], ['All items'], ['Untagged'], ['App Setup', 'app  setup'], []])
def test_bootstrap_rejects_reserved_duplicate_or_empty_taxonomy(labels):
    raw = json.dumps({'topics': [{'label': label, 'description': 'Description.'} for label in labels]})
    with pytest.raises(CodexError):
        wire.parse_bootstrap(raw)
