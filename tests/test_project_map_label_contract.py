"""Project-map generation advertises the label bounds its validator enforces."""
from copy import deepcopy
import json

import pytest

from backend.app.integrations.codex.brief import (
    MAP_LABEL_LIMIT, OUTPUT_SCHEMA, SECTION_NAMES, validate_sections,
)
from backend.app.integrations.codex.protocol import CodexError


def payload():
    sections = {name: [] for name in SECTION_NAMES}
    for name in ('overview', 'architecture', 'current_status'):
        sections[name] = [{'text': 'A complete project statement.', 'classification': 'reported',
                           'source_ids': ['source:1']}]
    return {'sections': sections, 'project_map': {
        'nodes': [{'id': 'first', 'label': 'First component', 'source_ids': ['source:1']},
                  {'id': 'second', 'label': 'Second component', 'source_ids': ['source:1']}],
        'edges': [{'from': 'first', 'to': 'second', 'label': 'Calls', 'inferred': True,
                   'source_ids': ['source:1']}]}}


@pytest.mark.parametrize('kind', ['nodes', 'edges'])
def test_schema_declares_existing_nonempty_hundred_character_map_label_contract(kind):
    label = OUTPUT_SCHEMA['properties']['project_map']['properties'][kind]['items']['properties']['label']
    assert MAP_LABEL_LIMIT == 100
    assert label == {'type': 'string', 'minLength': 1, 'maxLength': MAP_LABEL_LIMIT}


@pytest.mark.parametrize('kind', ['nodes', 'edges'])
@pytest.mark.parametrize('length', [1, 100])
def test_map_label_accepts_contract_boundaries_without_changing_persisted_fields(kind, length):
    value = payload()
    value['project_map'][kind][0]['label'] = 'x' * length
    before = deepcopy(value)
    sections, graph = validate_sections(json.dumps(value), [{'id': 'source:1'}])
    assert sections == before['sections']
    assert graph == before['project_map']
    assert value == before


@pytest.mark.parametrize('kind', ['nodes', 'edges'])
@pytest.mark.parametrize('label', ['x' * 101, '', ' \n\t', None, 123, True])
def test_map_label_keeps_strict_overflow_blank_and_type_rejection(kind, label):
    value = payload()
    value['project_map'][kind][0]['label'] = label
    with pytest.raises(CodexError, match='Project map returned an invalid label'):
        validate_sections(json.dumps(value), [{'id': 'source:1'}])


def test_existing_empty_project_map_remains_valid():
    value = payload()
    value['project_map'] = {'nodes': [], 'edges': []}
    _, graph = validate_sections(json.dumps(value), [{'id': 'source:1'}])
    assert graph == value['project_map']
