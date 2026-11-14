"""Malformed persisted tag catalogs fail explicitly before any publication."""
import pytest
from backend.app.integrations.codex.knowledge_previews import _validate_result
from backend.app.integrations.codex.protocol import CodexError

@pytest.mark.parametrize('items', [None, {}, ['bad'], [{}], [{'id': None}], [{'id': ''}]])
def test_malformed_item_catalog_is_explicit_error(items):
    result = {'published': False, 'publication_id': 'publication', 'knowledge': {'items': items},
              'brief': {}, 'baseline_brief': None, 'diagnostics': {}}
    with pytest.raises(CodexError, match='invalid item identities'):
        _validate_result(result, 1)
