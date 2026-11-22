import json
from uuid import UUID

import pytest

from backend.app.integrations import claude_session
from backend.app.integrations.conversation_import import ConversationImportError, parse_file


SESSION = str(UUID(int=1))


def _message(kind, identifier, parent, content, **extra):
    return {
        'type': kind, 'sessionId': SESSION, 'uuid': str(UUID(int=identifier)),
        'parentUuid': str(UUID(int=parent)) if parent is not None else None,
        'message': {'role': kind, 'content': content}, **extra,
    }


def _transcript(home, rows, workspace='project'):
    file = home / 'projects' / workspace / f'{SESSION}.jsonl'
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(b''.join(json.dumps(row).encode() + b'\n' for row in rows))
    return file


def test_claude_session_keeps_active_branch_and_only_visible_text(tmp_path, monkeypatch):
    launch = tmp_path / 'launch'
    monkeypatch.setattr(claude_session, 'claude_launch_home', lambda: launch)
    _transcript(launch, [
        {'type': 'session-owner', 'sessionId': SESSION, 'accountUuid': 'private-account'},
        {'type': 'custom-title', 'sessionId': SESSION, 'customTitle': 'Project review'},
        _message('user', 2, None, 'Question', cwd='/private/path'),
        _message('assistant', 3, 2, 'Older branch'),
        _message('assistant', 4, 2, [{'type': 'thinking', 'thinking': 'private reasoning'},
                                     {'type': 'text', 'text': 'Current answer'},
                                     {'type': 'tool_use', 'input': 'private command'}]),
        _message('user', 5, 4, [{'type': 'tool_result', 'content': 'private result'}]),
        _message('user', 9, 5, '<command-name>/model</command-name>\n<command-message>model</command-message>\n'
                              '<command-args>private model selection</command-args>'),
        _message('assistant', 6, 9, 'After the tool', timestamp='2026-11-22T10:00:00Z'),
        _message('assistant', 7, 6, 'Private subagent text', isSidechain=True),
        _message('user', 8, 6, 'Injected reminder', isMeta=True),
        {'type': 'ai-title', 'sessionId': SESSION, 'aiTitle': 'Only a fallback'},
    ])
    normalized, payload = claude_session.read_session(None, SESSION, 'launch')
    assert normalized['title'] == 'Project review'
    assert normalized['content'] == 'User:\nQuestion\n\nAssistant:\nCurrent answer\n\nAssistant:\nAfter the tool'
    assert normalized['reference'] == f'claude-code:{SESSION}'
    conversation = normalized['conversation']
    assert conversation['linked_session'] == {'provider': 'claude', 'profile': 'launch', 'id': SESSION}
    assert conversation['completeness'] == 'unknown'
    assert [m['id'] for m in conversation['messages']] == [str(UUID(int=i)) for i in (2, 4, 6)]
    assert conversation['messages'][-1]['timestamp'] == '2026-11-22T10:00:00Z'
    assert parse_file('conversation.json', payload)['content'] == normalized['content']
    for secret in (b'private', b'Older branch', b'Injected reminder', b'accountUuid'):
        assert secret not in payload


def test_claude_session_profile_detection_deduplication_and_conflict(tmp_path, monkeypatch):
    launch, alias, registered = (tmp_path / name for name in ('launch', 'alias', 'registered'))
    monkeypatch.setattr(claude_session, 'claude_launch_home', lambda: launch)
    monkeypatch.setattr(claude_session, 'claude_profile_entries', lambda _: [
        ('registered:1', 'Alias', alias), ('registered:2', 'Second profile', registered),
    ])
    _transcript(launch, [_message('user', 2, None, 'Question in the launch profile')])
    alias.symlink_to(launch, target_is_directory=True)
    normalized, _ = claude_session.read_session(None, SESSION)
    assert normalized['conversation']['linked_session']['profile'] == 'launch'

    _transcript(registered, [_message('user', 3, None, 'Question in the second profile')])
    with pytest.raises(ConversationImportError) as error:
        claude_session.read_session(None, SESSION)
    assert error.value.code == 'ambiguous_session'
    normalized, _ = claude_session.read_session(None, SESSION, 'registered:2', 'My linked session')
    assert 'second profile' in normalized['content']
    assert normalized['title'] == 'My linked session'
    assert normalized['conversation']['completeness'] == 'partial'


def test_claude_session_does_not_follow_transcript_or_workspace_symlinks(tmp_path, monkeypatch):
    launch, foreign = tmp_path / 'launch', tmp_path / 'foreign'
    monkeypatch.setattr(claude_session, 'claude_launch_home', lambda: launch)
    target = _transcript(foreign, [_message('user', 2, None, 'Foreign transcript')])
    fake = launch / 'projects' / 'workspace' / f'{SESSION}.jsonl'
    fake.parent.mkdir(parents=True)
    fake.symlink_to(target)
    (launch / 'projects' / 'workspace-link').symlink_to(target.parent, target_is_directory=True)
    with pytest.raises(ConversationImportError) as error:
        claude_session.read_session(None, SESSION, 'launch')
    assert error.value.code == 'session_not_found'


def test_claude_session_refuses_a_half_written_record(tmp_path, monkeypatch):
    launch = tmp_path / 'launch'
    monkeypatch.setattr(claude_session, 'claude_launch_home', lambda: launch)
    file = _transcript(launch, [_message('user', 2, None, 'Question')])
    with file.open('ab') as handle:
        handle.write(b'{"type":"assistant"')
    with pytest.raises(ConversationImportError, match='still writing'):
        claude_session.read_session(None, SESSION, 'launch')


def test_claude_session_workspace_budget_is_shared_across_profiles(tmp_path, monkeypatch):
    launch, registered = tmp_path / 'launch', tmp_path / 'registered'
    monkeypatch.setattr(claude_session, 'claude_launch_home', lambda: launch)
    monkeypatch.setattr(claude_session, 'claude_profile_entries', lambda _: [('registered:1', 'Second profile', registered)])
    monkeypatch.setattr(claude_session, '_MAX_WORKSPACES', 1)
    (launch / 'projects' / 'workspace').mkdir(parents=True)
    _transcript(registered, [_message('user', 2, None, 'Question')])
    with pytest.raises(ConversationImportError, match='workspace search limit'):
        claude_session.read_session(None, SESSION)
    assert 'Question' in claude_session.read_session(None, SESSION, 'registered:1')[0]['content']
