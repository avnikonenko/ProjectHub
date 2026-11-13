"""Malformed extraction diagnostics distinguish shape failures without source text."""
import json

import pytest

from backend.app.integrations.codex import knowledge
from backend.app.integrations.codex.protocol import CodexError


@pytest.mark.parametrize(('value', 'detail'), [
    (['PRIVATE SOURCE CONTENT'], 'expected a JSON object'),
    ({'items': [], 'PRIVATE SOURCE CONTENT': True}, 'expected only the items field'),
    ({}, 'expected only the items field'),
    ({'items': 'PRIVATE SOURCE CONTENT'}, 'items must be an array'),
    ({'items': None}, 'items must be an array'),
    ({'items': [{}] * (knowledge.MAX_ITEMS + 1)},
     f'{knowledge.MAX_ITEMS + 1} items exceeds the limit of {knowledge.MAX_ITEMS}'),
])
def test_shape_failure_explains_reason_without_revealing_output(value, detail):
    with pytest.raises(CodexError) as error:
        knowledge.validate_extraction(json.dumps(value), {'sources': [], 'primary_ids': []}, 1, 'version')
    message = str(error.value)
    assert 'invalid structure' in message
    assert detail in message
    assert 'PRIVATE SOURCE CONTENT' not in message


def test_empty_item_array_remains_a_valid_shape():
    assert knowledge.validate_extraction('{"items":[]}', {'sources': [], 'primary_ids': []}, 1, 'version') == []
