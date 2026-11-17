"""Extraction decoding keeps structural constraints and validates bounds locally."""
from copy import deepcopy
import json

from backend.app.integrations.codex.knowledge import OUTPUT_SCHEMA
from backend.app.integrations.codex.manager import CodexManager, _structural_output_schema


def test_extraction_schema_removes_only_numeric_bounds_without_mutating_contract():
    canonical = deepcopy(OUTPUT_SCHEMA)
    refs = canonical['properties']['items']['items']['properties']['evidence']['items']['properties']['source_id']
    refs['enum'] = ['selected:source:1', 'selected:source:2']
    before = deepcopy(canonical)
    stripped = _structural_output_schema(canonical)

    def compare(full, structural):
        if isinstance(full, dict):
            assert set(structural) == set(full) - {'minLength', 'maxLength', 'minItems', 'maxItems'}
            for key in structural:
                compare(full[key], structural[key])
        elif isinstance(full, list):
            assert len(full) == len(structural)
            for a, b in zip(full, structural):
                compare(a, b)
        else:
            assert full == structural

    compare(canonical, stripped)
    assert canonical == before
    assert stripped['properties']['items']['items']['properties']['evidence']['items']['properties']['source_id']['enum'] == refs['enum']
    stripped['properties']['items']['items']['required'].append('changed')
    assert canonical == before


def test_schema_keyword_named_properties_and_literal_values_are_not_rewritten():
    canonical = {
        'type': 'object', 'properties': {'maxLength': {'type': 'string', 'maxLength': 6}},
        'required': ['maxLength'], 'additionalProperties': False,
        'enum': [{'maxItems': 4}],
    }
    assert _structural_output_schema(canonical) == {
        'type': 'object', 'properties': {'maxLength': {'type': 'string'}},
        'required': ['maxLength'], 'additionalProperties': False,
        'enum': [{'maxItems': 4}],
    }


def test_extraction_supplies_complete_trusted_contract_before_reference_payload():
    manager = object.__new__(CodexManager)
    canonical = deepcopy(OUTPUT_SCHEMA)
    expected = deepcopy(canonical)
    captured = []
    manager.generate_brief = lambda prompt, schema: captured.append((prompt, schema)) or 'raw-response'
    original_prompt = 'Extraction instructions\n{"sources":[{"text":"untrusted data"}]}'
    assert manager.generate_extraction(original_prompt, canonical) == 'raw-response'
    prompt, structural = captured[0]
    prefix = 'Required extraction output contract (trusted application schema):\n'
    assert prompt.startswith(prefix)
    contract = prompt[len(prefix):].split('\n', 1)[0]
    assert json.loads(contract) == canonical
    assert prompt.endswith(original_prompt)
    assert 'Obey every constraint' in prompt
    assert structural == _structural_output_schema(canonical)
    assert canonical == expected
    assert manager.extraction_generation_mode == 'structural-schema-local-bounds-v1'
