import pytest

from backend.app.integrations.chatgpt_share import ShareReadError, canonical_url, parse_transcript


SHARE_ID = '01234567-89ab-cdef-0123-456789abcdef'
URL = f'https://chatgpt.com/share/{SHARE_ID}'


@pytest.mark.parametrize('url', [
    f'http://chatgpt.com/share/{SHARE_ID}',
    f'https://chatgpt.com.evil.test/share/{SHARE_ID}',
    f'https://evil.test@chatgpt.com/share/{SHARE_ID}',
    f'https://chatgpt.com:443/share/{SHARE_ID}',
    f'https://chatgpt.com/c/{SHARE_ID}',
    f'{URL}/other',
])
def test_only_public_share_urls(url):
    with pytest.raises(ValueError):
        canonical_url(url)


def test_extracts_visible_text_and_roles_in_order():
    assert canonical_url(f' {URL.upper()}/?utm_source=link#top ') == URL
    assert parse_transcript("""
      <title>ChatGPT - Project &amp; plans</title>
      <p data-user-message-copy>Which database?<br>Why?</p>
      <div data-assistant-markdown-root><p>Use <b>SQLite</b>.</p>
      <pre><code>def connect():
    return db</code></pre><button>Copy</button><script>ignored()</script></div>
    """) == ('Project & plans', 'User:\nWhich database?\nWhy?\n\nChatGPT:\n'
           'Use SQLite.\n\ndef connect():\n    return db')


def test_preview_metadata_is_not_a_transcript():
    with pytest.raises(ShareReadError, match='No readable transcript'):
        parse_transcript('<title>ChatGPT - Preview</title><a>Shared via ChatGPT</a>')


def test_reads_desktop_stream_without_importing_hidden_or_tool_messages():
    import json

    table = []

    def encode(value):
        ref = len(table)
        table.append(None)
        if isinstance(value, dict):
            value = {f'_{encode(key)}': encode(item) for key, item in value.items()}
        elif isinstance(value, list):
            value = [encode(item) for item in value]
        table[ref] = value
        return ref

    def message(role, text, hidden=False, recipient='all'):
        return {'message': {
            'author': {'role': role}, 'recipient': recipient,
            'metadata': {'is_visually_hidden_from_conversation': hidden},
            'content': {'content_type': 'text', 'parts': [text]},
        }}

    encode({'loaderData': {'routes/share.$shareId.($action)': {'serverResponse': {'data': {
        'title': 'Desktop conversation',
        'linear_conversation': [
            message('system', 'System context'),
            message('user', 'Which database?'),
            message('assistant', 'Hidden context', hidden=True),
            message('assistant', 'Tool call', recipient='tool'),
            message('assistant', 'Use SQLite.'),
        ],
    }}}}})
    stream = json.dumps(table) + '\n'
    script = f'<script>window.__reactRouterContext.streamController.enqueue({json.dumps(stream)});</script>'
    assert parse_transcript(script) == ('Desktop conversation', 'User:\nWhich database?\n\nChatGPT:\nUse SQLite.')
