import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from backend.app.integrations import chatgpt_share, conversation_import as imports

UUID = '12345678-1234-1234-1234-123456789abc'
CLAUDE = f'https://claude.ai/share/{UUID}'


def test_normalized_json_preserves_exact_messages_and_unicode_offsets():
    bodies = [' hi 🧪\nAssistant:\nquoted header', 'yes\nUser:\nquoted reply ']
    result = imports.parse_file('conversation.json', json.dumps({
        'title': 'Example', 'provider': 'chatgpt', 'completeness': 'complete',
        'messages': [{'role': role, 'content': body, 'id': str(i), 'timestamp': '2026-11-20'}
                     for i, (role, body) in enumerate(zip(('user', 'assistant'), bodies))],
    }), source_url='https://chatgpt.com/share/' + UUID + '?private=omitted')
    assert result['title'] == 'Example'
    assert result['reference'] == 'https://chatgpt.com/share/' + UUID
    metadata = result['conversation']
    assert metadata['completeness'] == 'complete' and metadata['source_status'] == 'provided'
    for i, (message, body) in enumerate(zip(metadata['messages'], bodies)):
        assert result['content'][message['start']:message['end']] == body
        assert message['id'] == str(i) and message['timestamp'] == '2026-11-20'


def test_claude_single_conversation_json():
    value = {'name': 'Claude example', 'chat_messages': [
        {'sender': 'human', 'uuid': 'm1', 'text': 'Question'},
        {'sender': 'assistant', 'text': 'Answer'},
    ]}
    result = imports.parse_file('claude.json', json.dumps(value).encode())
    assert result['conversation']['provider'] == 'claude'
    assert result['conversation']['completeness'] == 'unknown'
    assert result['conversation']['source_status'] == 'none'
    assert result['conversation']['messages'][0]['id'] == 'm1'


def test_markdown_headers_outside_fences_only_preserve_body_text():
    text = '# A conversation\n\n## User\nFirst 🧪\n```text\nAssistant:\nquoted\n```\n\n**Claude:**\nAnswer\n'
    result = imports.parse_file('conversation.md', text)
    records = result['conversation']['messages']
    assert len(records) == 2 and [item['role'] for item in records] == ['user', 'assistant']
    assert result['title'] == 'A conversation' and result['conversation']['provider'] == 'claude'
    assert result['content'][records[0]['start']:records[0]['end']] == 'First 🧪\n```text\nAssistant:\nquoted\n```\n\n'
    assert result['content'][records[1]['start']:records[1]['end']] == 'Answer\n'


@pytest.mark.parametrize('text', ['User:\nOnly one side', 'Assistant:\nOnly one side'])
def test_missing_role_is_partial(text):
    assert imports.parse_file('partial.md', text)['conversation']['completeness'] == 'partial'


@pytest.mark.parametrize('filename,content', [
    ('bad.json', '{bad'), ('bad.json', '[]'),
    ('bad.json', '{"messages":[{"role":"system","content":"x"}]}'),
    ('bad.json', '{"messages":[{"role":"user","content":123}]}'),
    ('bad.json', '{"messages":[{"role":"user","content":" "}]}'),
    ('bad.json', '{"messages":[{"role":"user","content":"x","id":5}]}'),
    ('bad.json', '{"messages":[{"role":"user","content":"\\ud800"}]}'),
    ('bad.md', 'No speaker boundaries'), ('bad.md', 'User:\n\nAssistant:\nanswer'),
    ('bad.md', 'User:\nhello\nSystem:\nunknown role'),
    ('bad.md', 'User:\nhello\nAssistant: inline ambiguous message'),
    ('bad.md', '**User:\nmalformed header'),
    ('bad.md', 'User:\n```\nAssistant:\nunterminated'),
    ('bad.html', '<p>not supported</p>'),
])
def test_rejects_invalid_or_ambiguous_files(filename, content):
    with pytest.raises(imports.ConversationImportError):
        imports.parse_file(filename, content)


@pytest.mark.parametrize('url', [
    'http://claude.ai/share/' + UUID, 'https://claude.ai.evil/share/' + UUID,
    'https://user@claude.ai/share/' + UUID, 'https://claude.ai:443/share/' + UUID,
    'https://127.0.0.1/share/' + UUID, 'https://claude.ai/api/chat_snapshots/' + UUID,
    'https://claude.ai/share/not-an-id',
])
def test_invalid_share_targets_rejected_before_network(monkeypatch, url):
    monkeypatch.setattr(imports, 'build_opener', lambda *args: pytest.fail('unexpected network'))
    with pytest.raises(imports.ConversationImportError) as caught:
        imports.read_share(url)
    assert caught.value.code == 'invalid_url'


class Response:
    def __init__(self, body):
        self.stream = io.BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read1(self, size):
        return self.stream.read(size)


def test_claude_reads_only_fixed_anonymous_endpoint(monkeypatch):
    captured = []
    def open_request(request, timeout):
        captured.append((request, timeout))
        return Response(json.dumps({'name': 'Public', 'completeness': 'complete', 'chat_messages': [
            {'sender': 'human', 'text': 'Question'}, {'sender': 'assistant', 'text': 'Answer'}]}).encode())
    monkeypatch.setattr(imports, 'build_opener', lambda handler: SimpleNamespace(open=open_request))
    result = imports.read_share(CLAUDE + '?tracking=secret')
    request, timeout = captured[0]
    assert request.full_url == f'https://claude.ai/api/chat_snapshots/{UUID}' and timeout == 15
    assert set(key.lower() for key in request.headers) == {'accept', 'user-agent'}
    assert result['conversation']['source_status'] == 'verified'
    assert result['conversation']['completeness'] == 'unknown'
    assert result['reference'] == CLAUDE


@pytest.mark.parametrize('status', [401, 403, 404, 302])
def test_claude_access_errors_are_actionable_without_private_response(monkeypatch, status):
    def fail(request, timeout):
        raise HTTPError(request.full_url, status, 'private auth detail', {}, io.BytesIO(b'private'))
    monkeypatch.setattr(imports, 'build_opener', lambda handler: SimpleNamespace(open=fail))
    with pytest.raises(imports.ConversationImportError) as caught:
        imports.read_share(CLAUDE)
    assert caught.value.code == ('access_denied' if status in (401, 403) else 'share_unavailable')
    assert caught.value.status == status and 'private' not in str(caught.value)
    assert f'HTTP {status}' in str(caught.value)


@pytest.mark.parametrize('body', [b'<html>Application shell</html>', b'{"name":"Empty shell"}'])
def test_claude_shell_without_data_is_not_a_conversation(monkeypatch, body):
    monkeypatch.setattr(imports, 'build_opener', lambda handler: SimpleNamespace(
        open=lambda request, timeout: Response(body)))
    with pytest.raises(imports.ConversationImportError) as caught:
        imports.read_share(CLAUDE)
    assert caught.value.code == 'no_transcript'


def test_claude_bounded_response(monkeypatch):
    monkeypatch.setattr(imports, '_MAX_BYTES', 20)
    monkeypatch.setattr(imports, 'build_opener', lambda handler: SimpleNamespace(
        open=lambda request, timeout: Response(b'x' * 21)))
    with pytest.raises(imports.ConversationImportError) as caught:
        imports.read_share(CLAUDE)
    assert caught.value.code == 'too_large'


def test_claude_redirect_policy_does_not_follow_redirects():
    assert chatgpt_share._NoRedirect().redirect_request(None, None, 302, '', {},
                                                       'https://other.example/') is None


def test_file_limit_and_plain_role_words(monkeypatch):
    result = imports.parse_file('roles.md', 'User:\nAssistant\nUser\nAssistant:\nAnswer')
    assert len(result['conversation']['messages']) == 2
    monkeypatch.setattr(imports, '_MAX_BYTES', 10)
    with pytest.raises(imports.ConversationImportError) as caught:
        imports.parse_file('large.md', b'x' * 11)
    assert caught.value.code == 'too_large'


def test_claude_invalid_sender_shape_returns_domain_error():
    with pytest.raises(imports.ConversationImportError):
        imports.parse_file('bad.json', json.dumps({'chat_messages': [{'sender': [], 'text': 'x'}]}))


def test_chatgpt_adapter_uses_trusted_message_boundaries(monkeypatch):
    html = ('<title>ChatGPT - Shared</title><div data-user-message-copy>Question</div>'
            '<div data-assistant-markdown-root><pre>User:\nnot another turn</pre></div>')
    monkeypatch.setattr(chatgpt_share, 'read_transcript', lambda url, **kwargs:
                        chatgpt_share.parse_transcript(html, **kwargs))
    result = imports.read_share('https://chatgpt.com/share/' + UUID)
    assert len(result['conversation']['messages']) == 2
    answer = result['conversation']['messages'][1]
    assert result['content'][answer['start']:answer['end']] == 'User:\nnot another turn'
    assert result['conversation']['provider'] == 'chatgpt'
    assert result['conversation']['source_status'] == 'verified'


def test_one_element_claude_export_preserves_source_url_and_text_block_fallback(monkeypatch):
    monkeypatch.setattr(imports, 'build_opener', lambda *args: pytest.fail('provided URL must not be fetched'))
    value = [{'name': 'Export', 'source_url': 'https://claude.ai/chat/original-conversation',
              'chat_messages': [
                  {'sender': 'human', 'uuid': 'question', 'text': '  ',
                   'content': [{'type': 'text', 'text': 'Exact question 🧪'}]},
                  {'sender': 'assistant', 'uuid': 'answer', 'text': 'Answer'},
              ]}]
    result = imports.parse_file('export.json', json.dumps(value))
    assert result['reference'] == 'https://claude.ai/chat/original-conversation'
    assert result['conversation']['source_status'] == 'provided'
    first = result['conversation']['messages'][0]
    assert result['content'][first['start']:first['end']] == 'Exact question 🧪'


def test_account_archive_requires_extracting_one_conversation():
    conversation = {'chat_messages': [{'sender': 'human', 'text': 'Question'}]}
    with pytest.raises(imports.ConversationImportError, match='Extract one conversation'):
        imports.parse_file('export.json', json.dumps([conversation, conversation]))


@pytest.mark.parametrize('ids', [('same', 'same'), ('same', ' same ')])
def test_duplicate_nonblank_ids_rejected(ids):
    value = {'messages': [{'role': role, 'content': 'Text', 'id': identifier}
                         for role, identifier in zip(('user', 'assistant'), ids)]}
    with pytest.raises(imports.ConversationImportError, match='unique'):
        imports.parse_file('export.json', json.dumps(value))


def test_blank_ids_omitted_without_false_duplicates():
    value = {'messages': [{'role': role, 'content': 'Text', 'id': ' '}
                         for role in ('user', 'assistant')]}
    result = imports.parse_file('export.json', json.dumps(value))
    assert all('id' not in item for item in result['conversation']['messages'])


@pytest.mark.parametrize('bad_metadata', [[], 'unstructured', None])
def test_metadata_objects_required(bad_metadata):
    value = {'messages': [{'role': 'user', 'content': 'Text'}], 'metadata': bad_metadata}
    with pytest.raises(imports.ConversationImportError, match='metadata must be an object'):
        imports.parse_file('export.json', json.dumps(value))
    value = {'messages': [{'role': 'user', 'content': 'Text', 'metadata': bad_metadata}]}
    with pytest.raises(imports.ConversationImportError, match='metadata must be an object'):
        imports.parse_file('export.json', json.dumps(value))


@pytest.mark.parametrize('url', [
    'http://example.com/chat', 'javascript:alert(1)', 'https://user:password@example.com/chat',
    'https://example.com/chat\nAuthorization:private', 'https://example.com:bad/chat',
    'https://example.com/with space',
])
def test_unsafe_provided_urls_rejected(url):
    value = {'source_url': url, 'messages': [{'role': 'user', 'content': 'Text'}]}
    with pytest.raises(imports.ConversationImportError) as caught:
        imports.parse_file('export.json', json.dumps(value))
    assert caught.value.code == 'invalid_url'


def test_form_url_overrides_file_url_and_share_provenance_is_canonical(monkeypatch):
    monkeypatch.setattr(imports, 'build_opener', lambda *args: pytest.fail('provided URL must not be fetched'))
    value = {'source_url': 'https://example.org/original', 'metadata': {'provider': 'claude'},
             'messages': [{'role': 'user', 'content': 'Text'}]}
    result = imports.parse_file('export.json', json.dumps(value), source_url=CLAUDE + '?tracking=omitted')
    assert result['reference'] == CLAUDE and result['conversation']['provider'] == 'claude'


@pytest.mark.parametrize('extra', [
    {'attachments': [{'name': 'image.png'}]},
    {'content': [{'type': 'image', 'source': 'hidden'}]},
    {'content': [{'type': 'text', 'text': 'Readable'}, {'type': 'tool_result', 'text': 'Hidden'}]},
])
def test_nontext_attachments_are_rejected_visibly(extra):
    value = {'chat_messages': [{'sender': 'human', 'text': 'Question', **extra}]}
    with pytest.raises(imports.ConversationImportError, match='not supported'):
        imports.parse_file('export.json', json.dumps(value))


@pytest.mark.parametrize('blocks', [{'type': 'image', 'source': 'omitted'}, 123, True,
                                  [{'type': 'text', 'text': {'invalid': 'shape'}}]])
def test_claude_nonblank_text_does_not_bypass_content_validation(blocks):
    value = {'completeness': 'complete', 'chat_messages': [
        {'sender': 'human', 'text': 'Question', 'content': blocks},
        {'sender': 'assistant', 'text': 'Answer'},
    ]}
    with pytest.raises(imports.ConversationImportError, match='not supported|supported'):
        imports.parse_file('export.json', json.dumps(value))


@pytest.mark.parametrize('null_id', [None, '', ' '])
def test_claude_id_falls_back_to_uuid_and_rejects_duplicates(null_id):
    value = {'chat_messages': [
        {'sender': 'human', 'text': 'Question', 'id': null_id, 'uuid': 'same'},
        {'sender': 'assistant', 'text': 'Answer', 'id': null_id, 'uuid': 'same'},
    ]}
    with pytest.raises(imports.ConversationImportError, match='unique'):
        imports.parse_file('export.json', json.dumps(value))
    value['chat_messages'][1]['uuid'] = 'different'
    result = imports.parse_file('export.json', json.dumps(value))
    assert [item['id'] for item in result['conversation']['messages']] == ['same', 'different']


@pytest.mark.parametrize('as_export_list', [False, True])
def test_claude_export_conversation_uuid_is_normalized_without_replacing_share_identity(as_export_list):
    value = {'uuid': UUID.upper(), 'chat_messages': [{'sender': 'human', 'text': 'Question'}]}
    result = imports.parse_file('export.json', json.dumps([value] if as_export_list else value),
                                source_url='https://claude.ai/share/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
    assert result['conversation']['provider_conversation_id'] == UUID
    assert result['reference'] == 'https://claude.ai/share/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    assert result['conversation']['source_status'] == 'provided'


@pytest.mark.parametrize('location', ['top', 'metadata'])
def test_normalized_claude_json_preserves_conversation_identity(location):
    value = {'provider': 'claude', 'messages': [{'role': 'user', 'content': 'Question'}]}
    target = value if location == 'top' else value.setdefault('metadata', {})
    target['provider_conversation_id'] = UUID.upper()
    result = imports.parse_file('normalized.json', json.dumps(value))
    assert result['conversation']['provider_conversation_id'] == UUID
    assert result['reference'] is None and result['conversation']['source_status'] == 'none'


@pytest.mark.parametrize('identifier', ['arbitrary', '', [], {}, 123,
                                       'https://claude.ai/chat/' + UUID, '{' + UUID + '}'])
def test_claude_export_rejects_unstable_conversation_identity(identifier):
    value = {'uuid': identifier, 'chat_messages': [{'sender': 'human', 'text': 'Question'}]}
    with pytest.raises(imports.ConversationImportError, match='UUID'):
        imports.parse_file('export.json', json.dumps(value))


def test_claude_export_rejects_conflicting_identity_claims():
    value = {'uuid': UUID, 'metadata': {'provider_conversation_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'},
             'chat_messages': [{'sender': 'human', 'text': 'Question'}]}
    with pytest.raises(imports.ConversationImportError, match='conflicting'):
        imports.parse_file('export.json', json.dumps(value))
