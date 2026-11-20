from base64 import b64decode
import hashlib
import io
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.integrations import chatgpt_share
from backend.app.integrations import conversation_import
from backend.app.integrations.codex.knowledge_sources import load_sources
from backend.app.main import create_app


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    with TestClient(create_app(tmp_path)) as client:
        projects = [client.post('/api/projects', json={'name': name}).json()['id'] for name in ('One', 'Two')]
        yield client, *[f'/api/projects/{pid}' for pid in projects]


def file_payload(messages, *, source=None, title='Test conversation'):
    result = {'filename': 'chat.json', 'content': '\ufeff' + json.dumps({'title': title, 'messages': messages}, ensure_ascii=False)}
    if source:
        result['source_url'] = source
    return result


def check(client, base, body):
    response = client.post(base + '/conversations/preview', json=body)
    assert response.status_code == 200, response.text
    return response.json()


def save(client, base, body, preview, **options):
    return client.post(base + '/conversations/import', json={**body, 'fingerprint': preview['fingerprint'], **options})


def test_preview_save_duplicate_and_immutable_download(api):
    client, base, other = api
    payload = file_payload([{'role': 'user', 'id': 'u1', 'content': 'Café 🧪?'},
                            {'role': 'assistant', 'content': 'Use the existing workflow.'}])
    preview = check(client, base, payload)
    assert preview['role_counts'] == {'user': 1, 'assistant': 1} and preview['completeness'] == 'unknown'
    assert preview['reference'] == '' and preview['source_status'] == 'none'
    assert client.get(base + '/notes').json() == []
    response = save(client, base, payload, preview)
    assert response.status_code == 201, response.text
    note = response.json()
    first, second = note['conversation']['messages']
    assert first['id'] == 'u1' and second['id'].startswith('import:assistant:')
    assert note['content'][first['start']:first['end']] == 'Café 🧪?'
    assert first['content_hash'] == hashlib.sha256('Café 🧪?'.encode()).hexdigest()
    q = {'q': 'workflow'}
    history = client.get(base + '/history/search', params=q).json()['results']
    assert [item['note_id'] for item in history] == [note['id']]
    assert client.get(other + '/history/search', params=q).json()['results'] == []
    search = client.get(base + '/search/all', params={**q, 'kind': 'conversations'}).json()['groups'][0]
    assert search['results'][0]['title'] == note['title']
    summary_note = client.get(base + '/overview', params={'compact': 'true', 'note_content': 'false'}).json()['notes'][0]
    assert summary_note['conversation']['completeness'] == 'unknown'
    assert 'messages' not in summary_note['conversation']
    sources_for_knowledge = load_sources(client.app.state.engine, note['project_id'])['sources']
    assert [(item['role'], item['text']) for item in sources_for_knowledge] == [
        ('user', 'Café 🧪?'), ('assistant', 'Use the existing workflow.')]
    sources = client.get(base + f"/conversations/{note['id']}/sources").json()
    assert len(sources) == 1 and 'payload' not in sources[0]
    route = f"/conversations/{note['id']}/sources/{sources[0]['id']}"
    original = client.get(base + route)
    assert original.content == payload['content'].encode()
    assert original.headers['content-disposition'].startswith('attachment')
    assert client.get(other + route).status_code == 404
    exported_sources = client.get(base + '/export').json()['conversation_import_sources']
    assert len(exported_sources) == 1 and exported_sources[0]['note_id'] == note['id']
    exported_bytes = b64decode(exported_sources[0]['payload_base64'], validate=True)
    assert exported_bytes == original.content
    assert hashlib.sha256(exported_bytes).hexdigest() == exported_sources[0]['content_hash']
    assert client.get(other + '/export').json()['conversation_import_sources'] == []
    duplicate = check(client, base, {**payload, 'filename': 'same-messages.json'})
    assert duplicate['existing'] == {'id': note['id'], 'title': note['title'], 'duplicate': True}
    assert save(client, base, {**payload, 'filename': 'same-messages.json'}, duplicate).status_code == 409
    assert check(client, other, payload)['existing'] is None
    assert client.patch(base + f"/notes/{note['id']}", json={'content': 'Do not overwrite offsets'}).status_code == 409
    assert client.patch(base + f"/notes/{note['id']}", json={'title': 'Renamed'}).status_code == 200


def test_replacement_revalidates_input_and_preserves_source_and_message_identity(api):
    client, base, other = api
    url = 'https://claude.ai/share/c265b990-bef4-4d55-bbdd-1e87bd03d8b5'
    first = {'role': 'user', 'content': 'Keep this question.'}
    old = file_payload([first, {'role': 'assistant', 'content': 'Old reply'}], source=url)
    first_save = save(client, base, old, check(client, base, old))
    assert first_save.status_code == 201, first_save.text
    first_note = first_save.json()
    new = file_payload([first, {'role': 'assistant', 'content': 'New reply'}], source=url)
    checked = check(client, base, new)
    assert checked['existing']['id'] == first_note['id'] and not checked['existing']['duplicate']
    assert save(client, base, new, checked).status_code == 409
    assert save(client, base, old, checked, replace_existing=True).status_code == 409
    assert save(client, other, new, checked, replace_existing=True).status_code == 409
    result = save(client, base, new, checked, replace_existing=True)
    assert result.status_code == 201, result.text
    note = result.json()
    assert note['id'] == first_note['id'] and 'New reply' in note['content']
    assert note['conversation']['messages'][0]['id'] == first_note['conversation']['messages'][0]['id']
    revisions = client.get(base + f"/conversations/{note['id']}/sources").json()
    assert len(revisions) == 2 and revisions[0]['content_hash'] != revisions[1]['content_hash']
    assert client.get(base + f"/conversations/{note['id']}/sources/{revisions[1]['id']}").content == old['content'].encode()


def test_partial_and_forbidden_sources_require_explicit_handling(api, monkeypatch):
    client, base, _ = api
    payload = {'filename': 'prompts.md', 'content': '## User\nFirst prompt\n\n## User\nSecond prompt\n'}
    checked = check(client, base, payload)
    assert checked['completeness'] == 'partial' and checked['role_counts'] == {'user': 2, 'assistant': 0}
    assert save(client, base, payload, checked).status_code == 422
    for body in (file_payload([]), {'filename': 'bad.json', 'content': '[broken'},
                 {'filename': 'bad.md', 'content': 'Missing speaker labels'}, {'filename': 'bad.json', 'content': '{"chat": []}'},
                 {'filename': 'ambiguous.json', 'content': '{"messages":[{"role":"user","role":"assistant","content":"Ambiguous"}]}'}):
        assert client.post(base + '/conversations/preview', json=body).status_code == 422
    assert client.get(base + '/notes').json() == []
    assert save(client, base, payload, checked, confirm_partial=True).status_code == 201

    def blocked(url):
        raise conversation_import.ConversationImportError('Claude returned HTTP 403. Import a file instead.',
                                                          code='access_denied', status=403)

    monkeypatch.setattr(conversation_import, 'read_share', blocked)
    response = client.post(base + '/conversations/preview', json={'url': 'https://claude.ai/share/unavailable'})
    assert response.status_code == 403 and 'Import a file' in response.json()['detail']
    assert len(client.get(base + '/notes').json()) == 1


@pytest.mark.parametrize('legacy', [False, True])
def test_claude_export_revision_matches_provider_conversation_without_url(api, legacy):
    client, base, other = api
    identity = '4eb1e4c5-e71c-44cf-a8f3-d39d36471359'
    export = {'uuid': identity, 'name': 'Claude export', 'chat_messages': [
        {'uuid': 'u1', 'sender': 'human', 'text': 'Original question.'},
        {'uuid': 'a1', 'sender': 'assistant', 'text': 'Original answer.'},
    ]}
    body = {'filename': 'claude.json', 'content': json.dumps(export)}
    saved = save(client, base, body, check(client, base, body))
    assert saved.status_code == 201, saved.text
    note_id = saved.json()['id']
    assert saved.json()['conversation']['provider_conversation_id'] == identity
    if legacy:
        with client.app.state.engine.begin() as db:
            db.execute(text("UPDATE notes SET conversation=json_remove(conversation, '$.provider_conversation_id') "
                            "WHERE project_id=:pid AND id=:nid"),
                       {'pid': saved.json()['project_id'], 'nid': note_id})
    export['chat_messages'].append({'uuid': 'u2', 'sender': 'human', 'text': 'New question.'})
    changed = {**body, 'content': json.dumps(export)}
    checked = check(client, base, changed)
    assert checked['existing'] == {'id': note_id, 'title': saved.json()['title'], 'duplicate': False}
    assert check(client, other, changed)['existing'] is None
    assert save(client, base, changed, checked).status_code == 409
    replacement = save(client, base, changed, checked, replace_existing=True)
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()['id'] == note_id
    assert len(client.get(base + '/notes').json()) == 1
    copies = client.get(base + '/export').json()['conversation_import_sources']
    assert [b64decode(copy['payload_base64']) for copy in copies] == [
        body['content'].encode(), changed['content'].encode()]
    different_export = {**export, 'uuid': '01759b54-7401-4657-8549-8b03a114ae2f'}
    assert check(client, base, {**body, 'content': json.dumps(different_export)})['existing'] is None


def test_same_messages_with_corrected_completeness_allow_explicit_replacement(api):
    client, base, _ = api
    chat = {'title': 'Export', 'completeness': 'partial', 'messages': [
        {'role': 'user', 'content': 'Question.'},
        {'role': 'assistant', 'content': 'Answer.'},
    ]}
    link = 'https://claude.ai/share/c265b990-bef4-4d55-bbdd-1e87bd03d8b5'
    body = {'filename': 'transcript.json', 'content': json.dumps(chat), 'source_url': link}
    saved = save(client, base, body, check(client, base, body), confirm_partial=True)
    assert saved.status_code == 201, saved.text
    corrected = {**body, 'content': json.dumps({**chat, 'completeness': 'complete'})}
    checked = check(client, base, corrected)
    assert checked['existing']['duplicate'] is False
    replacement = save(client, base, corrected, checked, replace_existing=True)
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()['id'] == saved.json()['id']
    assert replacement.json()['conversation']['completeness'] == 'complete'
    assert check(client, base, corrected)['existing']['duplicate'] is True


def test_file_duplicate_matches_legacy_chatgpt_labels(api, monkeypatch):
    client, base, _ = api

    url = 'https://chatgpt.com/share/c265b990-bef4-4d55-bbdd-1e87bd03d8b5'
    monkeypatch.setattr(chatgpt_share, 'read_transcript',
                        lambda url: ('Legacy', 'User:\nQuestion.\n\nChatGPT:\nAnswer.'))
    saved = client.post(base + '/notes/chatgpt-share', json={'url': url})
    assert saved.status_code == 201, saved.text
    messages = [{'role': 'user', 'content': 'Question.'}, {'role': 'assistant', 'content': 'Answer.'}]
    for source in (None, url):
        body = file_payload(messages, source=source)
        checked = check(client, base, body)
        assert checked['existing'] == {'id': saved.json()['id'], 'title': 'Legacy', 'duplicate': True}
        assert save(client, base, body, checked).status_code == 409
    assert len(client.get(base + '/notes').json()) == 1


def test_import_accepts_valid_markdown_above_generic_request_limit(api):
    client, base, _ = api
    body = {'filename': 'large.md',
            'content': '## User\n' + 'x' * (3 * 1024 * 1024) + '\n\n## Assistant\nAnswer.'}
    checked = check(client, base, body)
    assert checked['message_count'] == 2
    result = save(client, base, body, checked)
    assert result.status_code == 201
    sources = client.get(base + f"/conversations/{result.json()['id']}/sources").json()
    assert sources[0]['content_hash'] == hashlib.sha256(body['content'].encode()).hexdigest()


def test_public_claude_link_preview_and_save_keep_snapshot_source(api, monkeypatch):
    client, base, _ = api
    share_id = 'c265b990-bef4-4d55-bbdd-1e87bd03d8b5'
    conversation_id = '4eb1e4c5-e71c-44cf-a8f3-d39d36471359'
    link = f'https://claude.ai/share/{share_id}'
    snapshot = {'uuid': conversation_id, 'name': 'Shared Claude conversation', 'chat_messages': [
        {'sender': 'human', 'id': None, 'uuid': 'u1', 'text': 'Original question.'},
        {'sender': 'assistant', 'uuid': 'a1', 'text': 'Original answer.'},
    ]}
    requests = []

    def open_snapshot(request, timeout):
        requests.append(request.full_url)
        return io.BufferedReader(io.BytesIO(json.dumps(snapshot).encode()))

    monkeypatch.setattr(conversation_import, 'build_opener', lambda handler: SimpleNamespace(open=open_snapshot))
    body = {'url': link}
    checked = check(client, base, body)
    assert (checked['source_status'], checked['completeness']) == ('verified', 'unknown')
    assert client.get(base + '/notes').json() == []
    result = save(client, base, body, checked)
    assert result.status_code == 201, result.text
    record = result.json()
    assert record['reference'] == link
    assert [msg['id'] for msg in record['conversation']['messages']] == ['u1', 'a1']
    assert requests == [f'https://claude.ai/api/chat_snapshots/{share_id}'] * 2
    source = client.get(base + '/export').json()['conversation_import_sources'][0]
    retained = json.loads(b64decode(source['payload_base64']))
    assert retained['source_url'] == link
    assert [msg['content'] for msg in retained['messages']] == ['Original question.', 'Original answer.']
