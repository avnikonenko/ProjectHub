import copy
import json

import pytest

from backend.app.integrations.codex import compact_topic_wire as wire
from backend.app.integrations.codex.protocol import CodexError


def record(index, category='Requirement', scope='Project', statement=None):
    return {'id': str(index), 'category': category, 'scope': scope,
            'statement': statement or f'Statement {index}', 'rationale': 'private context'}


def catalog():
    return [{'id': 'legacy:stable', 'label': 'Quality Checks', 'description': 'Existing definition.'}]


def reply(*topics):
    return json.dumps({'topics': [{'label': label, 'description': description} for label, description in topics]})


def test_review_sample_is_order_independent_prioritized_and_spread_across_scopes():
    records = [record(i, category, f'Scope {i % 8}')
               for category in ('Requirement', 'Decision', 'Finding', 'Proposal') for i in range(100)]
    original = copy.deepcopy(records)
    payload = wire.review_payload(records, catalog())
    assert payload == wire.review_payload(list(reversed(records)), catalog())
    assert records == original
    assert len(payload['sample']) == wire.SAMPLE_LIMIT
    counts = {category: sum(entry['category'] == category for entry in payload['sample'])
              for category in ('Requirement', 'Decision', 'Finding', 'Proposal')}
    assert counts == {'Requirement': 16, 'Decision': 16, 'Finding': 8, 'Proposal': 8}
    assert len({entry['scope'] for entry in payload['sample']}) == 8
    assert payload['topics'] == catalog()
    assert all(set(entry) == {'category', 'statement', 'scope'} for entry in payload['sample'])
    assert len(json.dumps({'sample': payload['sample']}, ensure_ascii=False, separators=(',', ':'))) <= wire.SAMPLE_CHARS


def test_review_sample_fills_unused_quota_and_omits_oversize_records():
    records = [record('huge', statement='x' * wire.SAMPLE_CHARS)] + [record(i) for i in range(60)]
    sample = wire.review_payload(records, catalog())['sample']
    assert len(sample) == wire.SAMPLE_LIMIT
    assert all('scope' not in entry for entry in sample)
    assert not any(entry['statement'].startswith('xxx') for entry in sample)
    with pytest.raises(CodexError):
        wire.review_payload(records[:1], catalog())
    assert len(wire.review_payload([record(i, 'Finding') for i in range(60)], [])['sample']) == 48


def test_review_spreads_scope_selection_without_alphabetical_prefix_bias():
    records = [record(i, scope=f'Scope {i:04d}') for i in range(200)]
    scopes = {entry['scope'] for entry in wire.review_payload(records, [])['sample']}
    assert len(scopes) == 48
    assert any(scope > 'Scope 0100' for scope in scopes)
    assert any(scope < 'Scope 0100' for scope in scopes)


def test_review_preserves_existing_identity_and_uses_bootstrap_canonical_ids():
    existing = catalog()
    original = copy.deepcopy(existing)
    raw = reply(('quality   CHECKS', 'A proposed replacement.'), ('New Subject', 'Distinct subject.'))
    result = wire.parse_review(raw, existing)
    assert existing == original
    assert result[0] == original[0]
    assert result[1] == wire.parse_bootstrap(reply(('New Subject', 'Distinct subject.')))[0]
    result[0]['description'] = 'Changed outside parser'
    assert existing == original
    assert wire.parse_review(reply(), existing) == existing
    assert wire.REVIEW_SCHEMA['properties']['topics']['minItems'] == 0
    assert wire.REVIEW_SCHEMA['properties']['topics']['maxItems'] == 12


@pytest.mark.parametrize('raw', [
    '{}', '{"topics":null}', '{"topics":[],"extra":1}', '{"topics":[],"topics":[]}',
    '{"topics":[null]}', '{"topics":[{"label":1,"description":"Description"}]}',
    '{"topics":[{"label":"Subject","description":null}]}',
    '{"topics":[{"label":"Subject","description":"Description","id":"evil"}]}',
    reply((' Subject', 'Description')), reply(('Subject', '')), reply(('All', 'Description')),
    reply(('Other', 'Description')), reply(('All items', 'Description')), reply(('Untagged', 'Description')),
    reply(('a' * 81, 'Description')), reply(('Subject', 'a' * 401)),
    reply(('Subject', 'Description'), ('subject', 'Description')),
    reply(*[(f'Subject {i}', 'Description') for i in range(13)]), 'x' * (wire.MAX_REPLY_CHARS + 1),
])
def test_review_rejects_invalid_untrusted_output(raw):
    with pytest.raises(CodexError):
        wire.parse_review(raw, catalog())


def test_review_validates_existing_duplicate_proposals_and_catalog_limit():
    with pytest.raises(CodexError):
        wire.parse_review(reply(('Quality Checks', '')), catalog())
    existing = [{'id': f'legacy:{i}', 'label': f'Topic {i}', 'description': 'Definition.'} for i in range(400)]
    assert wire.parse_review(reply(('topic 1', 'Same subject.')), existing) == existing
    with pytest.raises(CodexError):
        wire.parse_review(reply(('Additional topic', 'Definition.')), existing)
    new = wire.parse_bootstrap(reply(('Additional topic', 'Definition.')))[0]
    with pytest.raises(CodexError):
        wire.parse_review(reply(('Additional topic', 'Definition.')), [{**catalog()[0], 'id': new['id']}])
