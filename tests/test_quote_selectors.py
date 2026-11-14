"""Lossless wire identity tests, not semantic verification of model claims."""
from copy import deepcopy
import hashlib
import json

import pytest

from backend.app.integrations.codex import knowledge
from backend.app.integrations.codex.protocol import CodexError
from backend.app.integrations.codex.quote_selectors import (
    prepare_quote_selection, restore_quote_selection,
)


def source(identifier, text):
    return {'id': identifier, 'text': text, 'role': 'assistant', 'conversation_id': 'conversation',
            'order': 1, 'revision': hashlib.sha256(text.encode()).hexdigest(), 'metadata': {'nested': [1, 2]}}


def payload(*sources):
    return {'sources': list(sources), 'primary_ids': [sources[-1]['id']] if sources else [],
            'context_ids': [s['id'] for s in sources[:-1]], 'other_metadata': {'unchanged': True}}


def response(identifier, selector):
    return {'items': [{'statement': 'The source describes an optional command-line flag.',
                       'category': 'PROPOSAL', 'design_status': 'PROPOSED', 'scope': 'CLI configuration',
                       'rationale': '', 'limitations': '', 'alternatives': '', 'agreement_basis': 'none',
                       'revises_statement': '', 'conflicting_statements': [],
                       'evidence': [{'source_id': identifier, 'role': 'assistant', 'excerpt': selector}]}]}


def test_source_text_is_rendered_once_losslessly_with_schema_bounded_chunks():
    text = '\n\nHeading\r\n\r\n' + 'one line\n' * 300 + '💡' * 3201 + '\nfinal  \t\n'
    original = payload(source('opaque-source', text))
    schema = deepcopy(knowledge.OUTPUT_SCHEMA)
    before = deepcopy((original, schema))
    wire, wire_schema, catalog = prepare_quote_selection(original, schema)
    record, = wire['sources']
    assert 'text' not in record
    assert ''.join(quote['text'] for quote in record['literal_quotes']) == text
    assert all(len(quote['text']) <= 1600 for quote in record['literal_quotes'])
    assert [quote['id'] for quote in record['literal_quotes']] == list(catalog)
    for quote in record['literal_quotes']:
        assert catalog[quote['id']] == ('opaque-source', quote['text'])
    expected_record = deepcopy(original['sources'][0])
    del expected_record['text']
    assert {k: v for k, v in record.items() if k != 'literal_quotes'} == expected_record
    assert {k: v for k, v in wire.items() if k != 'sources'} == {k: v for k, v in original.items() if k != 'sources'}
    expected_schema = deepcopy(schema)
    expected_schema['properties']['items']['items']['properties']['evidence']['items']['properties']['excerpt']['enum'] = [
        identifier for identifier, (_, literal) in catalog.items() if literal.strip()]
    assert wire_schema == expected_schema
    assert (original, schema) == before


def test_existing_line_and_paragraph_boundaries_are_preferred_without_normalizing():
    first, second = 'a' * 900 + '\r\n', 'b' * 900 + '\n\n'
    wire, _, _ = prepare_quote_selection(payload(source('opaque', first + second + 'last')), knowledge.OUTPUT_SCHEMA)
    assert [q['text'] for q in wire['sources'][0]['literal_quotes']] == [first, second, 'last']


def test_blank_chunks_remain_lossless_but_cannot_be_selected_as_evidence():
    text = '\n\nFirst paragraph.\n\n\n\nSecond paragraph.\n\n\n'
    wire, schema, catalog = prepare_quote_selection(payload(source('mixed', text)), knowledge.OUTPUT_SCHEMA)
    quotes = wire['sources'][0]['literal_quotes']
    assert ''.join(quote['text'] for quote in quotes) == text
    assert [quote['id'] for quote in quotes] == list(catalog)
    allowed = schema['properties']['items']['items']['properties']['evidence']['items']['properties']['excerpt']['enum']
    blanks = [identifier for identifier, (_, literal) in catalog.items() if not literal.strip()]
    assert blanks and allowed
    assert set(allowed) == set(catalog) - set(blanks)
    for identifier in blanks:
        with pytest.raises(CodexError, match='whitespace-only quotation'):
            restore_quote_selection(json.dumps(response('mixed', identifier)), catalog, knowledge.MAX_RESPONSE_CHARS)


@pytest.mark.parametrize('text', ['', ' ', '\r\n\t', '\u2003'])
def test_empty_or_whitespace_only_source_stops_before_a_model_request(text):
    original = payload(source('empty', text))
    before = deepcopy(original)
    with pytest.raises(CodexError, match='invalid source text'):
        prepare_quote_selection(original, knowledge.OUTPUT_SCHEMA)
    assert original == before


def test_no_sources_cannot_invent_a_quote_selector():
    with pytest.raises(CodexError, match='no usable source'):
        prepare_quote_selection(payload(), knowledge.OUTPUT_SCHEMA)


def test_code_typo_and_all_other_output_fields_restore_exactly_without_repair():
    text = ('    run.add_argument("--claude-tools", default="Bash,Read,Write",\n'
            '                     help=\'comma-separated built-in Claude tools to load ("default" for \'\n'
            '                          "Claude\'s full set); has no effect for --agent codex")\n')
    original = payload(source('original-code', text))
    wire, _, catalog = prepare_quote_selection(original, knowledge.OUTPUT_SCHEMA)
    result = response('original-code', wire['sources'][0]['literal_quotes'][0]['id'])
    before = deepcopy((result, wire, dict(catalog)))
    restored = json.loads(restore_quote_selection(json.dumps(result), catalog, knowledge.MAX_RESPONSE_CHARS))
    expected = deepcopy(result)
    expected['items'][0]['evidence'][0]['excerpt'] = text
    assert restored == expected
    assert (result, wire, dict(catalog)) == before
    window = {'sources': original['sources'], 'primary_ids': original['primary_ids'], 'context_ids': []}
    validated = knowledge.validate_extraction(json.dumps(restored), window, 1, 'unchanged-version')
    assert validated[0]['evidence'][0]['excerpt'] == text


def test_identical_source_text_does_not_make_quote_owners_interchangeable():
    original = payload(source('first', 'Identical exact text.'), source('second', 'Identical exact text.'))
    wire, _, catalog = prepare_quote_selection(original, knowledge.OUTPUT_SCHEMA)
    first_id = wire['sources'][0]['literal_quotes'][0]['id']
    second_id = wire['sources'][1]['literal_quotes'][0]['id']
    assert first_id != second_id and catalog[first_id][1] == catalog[second_id][1]
    with pytest.raises(CodexError, match='owned by another source'):
        restore_quote_selection(json.dumps(response('second', first_id)), catalog, knowledge.MAX_RESPONSE_CHARS)


@pytest.mark.parametrize('selector', ['missing', 'Exact quote text.', None, [], 1])
def test_only_known_request_local_selectors_can_restore_evidence(selector):
    _, _, catalog = prepare_quote_selection(payload(source('source', 'Exact quote text.')), knowledge.OUTPUT_SCHEMA)
    with pytest.raises(CodexError, match='unknown quote selector'):
        restore_quote_selection(json.dumps(response('source', selector)), catalog, knowledge.MAX_RESPONSE_CHARS)


def test_source_roles_are_not_rewritten_and_canonical_validator_still_checks_them():
    original = payload(source('source', 'A literal original passage.'))
    _, _, catalog = prepare_quote_selection(original, knowledge.OUTPUT_SCHEMA)
    result = response('source', 'q1')
    result['items'][0]['evidence'][0]['role'] = 'user'
    canonical = restore_quote_selection(json.dumps(result), catalog, knowledge.MAX_RESPONSE_CHARS)
    assert json.loads(canonical)['items'][0]['evidence'][0]['role'] == 'user'
    with pytest.raises(CodexError, match='speaker'):
        knowledge.validate_extraction(canonical, {**original, 'context_ids': []}, 1, 'unchanged')


@pytest.mark.parametrize('raw', ['not JSON', '[]', '{"items":{}}', '{"items":[{"evidence":[]}]}',
                               '{"items":[],"items":[]}', '{"items":[],"x":NaN}'])
def test_json_structure_and_duplicate_fields_are_strictly_checked(raw):
    _, _, catalog = prepare_quote_selection(payload(source('source', 'Literal.')), knowledge.OUTPUT_SCHEMA)
    with pytest.raises(CodexError):
        restore_quote_selection(raw, catalog, knowledge.MAX_RESPONSE_CHARS)


def test_array_bounds_come_from_the_supplied_schema():
    schema = deepcopy(knowledge.OUTPUT_SCHEMA)
    schema['properties']['items']['maxItems'] = 1
    schema['properties']['items']['items']['properties']['evidence']['maxItems'] = 1
    _, _, catalog = prepare_quote_selection(payload(source('source', 'Literal.')), schema)
    value = response('source', 'q1')
    with pytest.raises(CodexError, match='invalid items'):
        restore_quote_selection(json.dumps({'items': value['items'] * 2}), catalog, knowledge.MAX_RESPONSE_CHARS)
    value['items'][0]['evidence'] *= 2
    with pytest.raises(CodexError, match='invalid evidence'):
        restore_quote_selection(json.dumps(value), catalog, knowledge.MAX_RESPONSE_CHARS)


def test_both_wire_and_restored_response_sizes_are_bounded():
    _, _, catalog = prepare_quote_selection(payload(source('source', 'x' * 1000)), knowledge.OUTPUT_SCHEMA)
    raw = json.dumps(response('source', 'q1'))
    with pytest.raises(CodexError, match='response exceeds'):
        restore_quote_selection(raw, catalog, len(raw) - 1)
    with pytest.raises(CodexError, match='Restored quote selection response exceeds'):
        restore_quote_selection(raw, catalog, len(raw) + 10)


def test_each_request_needs_its_own_catalog_and_valid_numeric_bound():
    with pytest.raises(CodexError, match='request-local catalog'):
        restore_quote_selection('{"items":[]}', {}, 100)
    _, _, catalog = prepare_quote_selection(payload(source('source', 'Literal.')), knowledge.OUTPUT_SCHEMA)
    with pytest.raises(CodexError, match='positive schema bound'):
        restore_quote_selection('{"items":[]}', catalog, True)
