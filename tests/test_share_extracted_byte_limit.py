"""Expanded share content obeys the same UTF-8 byte budget as note editing."""
import json
import random
import re

import pytest

from backend.app.integrations import chatgpt_share as share


def stream_page(messages):
    table, strings = [], {}

    def encode(value):
        if isinstance(value, str) and value in strings:
            return strings[value]
        index = len(table)
        table.append(None)
        if isinstance(value, dict):
            out = {f'_{encode(key)}': encode(item) for key, item in value.items()}
        elif isinstance(value, list):
            out = [encode(item) for item in value]
        else:
            out = value
        table[index] = out
        if isinstance(value, str):
            strings[value] = index
        return index

    nodes = [{'message': {'author': {'role': role}, 'recipient': 'all', 'metadata': {},
                         'content': {'content_type': 'text', 'parts': parts}}}
             for role, parts in messages]
    encode({'loaderData': {'routes/share.$shareId.($action)': {'serverResponse': {'data': {
        'title': 'Expanded limit fixture', 'linear_conversation': nodes,
    }}}}})
    stream = json.dumps(table, ensure_ascii=False) + '\n'
    return '<script>window.__reactRouterContext.streamController.enqueue(' + json.dumps(stream, ensure_ascii=False) + ');</script>'


@pytest.mark.parametrize('messages,expected', [
    ([('user', ['abc'])], 'User:\nabc'),
    ([('user', ['éé']), ('assistant', ['😀'])], 'User:\néé\n\nChatGPT:\n😀'),
    ([('user', ['é', '😀', 'xyz'])], 'User:\né\n😀\nxyz'),
    ([('assistant', ['  ', '\t']), ('user', ['kept'])], 'User:\nkept'),
])
def test_stream_budget_counts_exact_emitted_bytes_labels_and_separators(monkeypatch, messages, expected):
    html = stream_page(messages)
    size = len(expected.encode('utf-8'))
    monkeypatch.setattr(share, '_MAX_BYTES', size)
    assert share.parse_transcript(html)[1] == expected
    monkeypatch.setattr(share, '_MAX_BYTES', size - 1)
    with pytest.raises(share.ShareReadError, match='too large'):
        share.parse_transcript(html)


def test_small_wire_repeated_references_cannot_expand_beyond_note_byte_limit():
    html = stream_page([('user' if i % 2 == 0 else 'assistant', ['é' * 100_000]) for i in range(100)])
    assert len(html.encode('utf-8')) < 300_000
    with pytest.raises(share.ShareReadError, match='too large'):
        share.parse_transcript(html)


@pytest.mark.parametrize('html,expected', [
    ('<p data-user-message-copy>abc</p>', 'User:\nabc'),
    ('<p data-user-message-copy>éé</p><div data-assistant-markdown-root>😀</div>', 'User:\néé\n\nChatGPT:\n😀'),
    ('<div data-user-message-copy>  <p>abc</p><br><br><p>é</p>  </div>', 'User:\nabc\n\né'),
])
def test_legacy_dom_budget_counts_normalized_utf8_output(monkeypatch, html, expected):
    size = len(expected.encode('utf-8'))
    monkeypatch.setattr(share, '_MAX_BYTES', size)
    assert share.parse_transcript(html)[1] == expected
    monkeypatch.setattr(share, '_MAX_BYTES', size - 1)
    with pytest.raises(share.ShareReadError, match='too large'):
        share.parse_transcript(html)


def test_legacy_rejects_data_before_finishing_and_joining_oversized_message(monkeypatch):
    monkeypatch.setattr(share, '_MAX_BYTES', 64)
    finished = []
    original = share._MessageText.finish

    def finish(self):
        finished.append(True)
        return original(self)

    monkeypatch.setattr(share._MessageText, 'finish', finish)
    with pytest.raises(share.ShareReadError, match='too large'):
        share.parse_transcript('<p data-user-message-copy>' + 'é' * 40 + '</p>')
    assert finished == []


def test_legacy_whitespace_normalization_survives_fragment_boundaries():
    rng = random.Random(537)
    for _ in range(200):
        chunks = [''.join(rng.choice('abé😀 \n\t\r') for _ in range(rng.randrange(40))) for _ in range(10)]
        expected = re.sub(r'\n{3,}', '\n\n', ''.join(chunks)).strip()
        collector = share._MessageText(len(expected.encode('utf-8')))
        for chunk in chunks:
            collector.append(chunk)
        assert collector.finish() == expected
        assert collector.size == len(expected.encode('utf-8'))


def test_discarded_trailing_whitespace_does_not_fill_retained_fragments():
    collector = share._MessageText(8)
    collector.append('body')
    collector.append(' ' * 100_000)
    assert collector.finish() == 'body'
    assert collector.pending == []
    with pytest.raises(share.ShareReadError, match='too large'):
        collector.append('suffix')
