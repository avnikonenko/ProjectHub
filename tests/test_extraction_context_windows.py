"""Bounded source batching regressions; these do not measure model accuracy."""
from collections import Counter
from copy import deepcopy
import hashlib
import json

import pytest

from backend.app.integrations.codex.knowledge_sources import build_windows


def source(identifier, text, role='assistant', conversation='discussion'):
    return {'id': identifier, 'conversation_id': conversation, 'role': role,
            'text': text, 'revision': hashlib.sha256(text.encode()).hexdigest()}


@pytest.mark.parametrize('max_chars', [2000, 14000, 24000])
def test_smaller_windows_preserve_sources_and_adjacent_acceptance_context(max_chars):
    # Enough long messages to force boundaries for every tested cap. The user
    # acceptance must retain its immediately preceding proposal even when the
    # proposal becomes context rather than a primary message.
    padding = 'x' * (max_chars // 4 - 200)
    sources = [source(str(index), 'Consider option ' + str(index) + '. ' + padding)
               for index in range(8)]
    sources.append(source('acceptance', 'Yes, adopt option 7.', role='user'))
    original = deepcopy(sources)
    windows = build_windows({'sources': sources}, max_chars=max_chars)
    assert len(windows) > 1
    assert [identifier for window in windows for identifier in window['primary_ids']] == [
        item['id'] for item in sources]
    assert Counter(identifier for window in windows for identifier in window['primary_ids']) == {
        item['id']: 1 for item in sources}
    for window in windows:
        assert len(json.dumps(window, ensure_ascii=False)) <= max_chars
        assert not set(window['primary_ids']) & set(window['context_ids'])
        assert all(item == original[int(item['id'])] if item['id'].isdigit()
                   else item == original[-1] for item in window['sources'])
    acceptance_window = next(window for window in windows if 'acceptance' in window['primary_ids'])
    assert any(item['id'] == '7' and item['role'] == 'assistant'
               for item in acceptance_window['sources'])
    assert sources == original


def test_default_window_limit_is_smaller_without_losing_primary_messages():
    sources = [source(str(index), 'Meaningful original text.\n' * 230) for index in range(9)]
    default = build_windows({'sources': sources})
    previous = build_windows({'sources': sources}, max_chars=24000)
    assert default == build_windows({'sources': sources}, max_chars=14000)
    assert len(default) > len(previous)
    assert all(len(json.dumps(window, ensure_ascii=False)) <= 14000 for window in default)
    assert [identifier for window in default for identifier in window['primary_ids']] == [
        item['id'] for item in sources]


def test_context_stays_with_its_conversation_and_closed_windows_remain_cacheable():
    first = [source(str(index), 'Original message.\n' * 350) for index in range(5)]
    second = source('other', 'Yes.', role='user', conversation='other discussion')
    before = build_windows({'sources': [*first, second]})
    other_window = next(window for window in before if 'other' in window['primary_ids'])
    assert other_window['context_ids'] == []
    assert [item['id'] for item in other_window['sources']] == ['other']
    after = build_windows({'sources': [*first, second,
        source('appended', 'Keep the rationale.', role='user', conversation='other discussion')]})
    first_conversation = [window for window in before if 'other' not in window['primary_ids']]
    assert after[:len(first_conversation)] == first_conversation


@pytest.mark.parametrize('max_chars', [True, 999, 14000.0, '14000'])
def test_window_limit_rejects_invalid_configuration(max_chars):
    with pytest.raises(ValueError, match='integer of at least'):
        build_windows({'sources': []}, max_chars=max_chars)
