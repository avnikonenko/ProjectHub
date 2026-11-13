import json
from types import SimpleNamespace

import pytest

from scripts import notes_cli


def test_note_preserves_literal_content_and_session(monkeypatch, capsys):
    calls = []
    def request(method, path, payload=None):
        calls.append((method, path, payload))
        return [{'id': 3, 'name': 'Website'}] if method == 'GET' else {'id': 9, 'project_id': 3, 'kind': 'ai_note'}
    monkeypatch.setattr(notes_cli, 'request', request)
    monkeypatch.setenv('CODEX_THREAD_ID', 'session-123')
    content = 'Keep `code` and $(literal text)\nSecond line.'
    assert notes_cli.main(['note', 'add', '--project', 'Website', '--title', 'Finding', '--content', content]) == 0
    assert calls[-1] == ('POST', '/projects/3/notes', {
        'title': 'Finding', 'content': content, 'kind': 'ai_note',
        'reference': 'AI terminal session session-123',
    })
    assert json.loads(capsys.readouterr().out)['note_id'] == 9


def test_ambiguous_project_never_writes():
    with pytest.raises(ValueError, match='ambiguous'):
        notes_cli.resolve_project([{'id': 1, 'name': 'Same'}, {'id': 2, 'name': 'Same'}],
                                  SimpleNamespace(project_id=None, project='Same'))


def test_failed_post_is_not_retried(monkeypatch):
    calls = []
    def request(method, path, payload=None):
        calls.append(method)
        if method == 'GET':
            return [{'id': 1, 'name': 'Project'}]
        raise ValueError('Unknown delivery status; check Notes before retrying.')
    monkeypatch.setattr(notes_cli, 'request', request)
    assert notes_cli.main(['note', 'add', '--project-id', '1', '--title', 'Finding', '--content', 'Text']) == 1
    assert calls == ['GET', 'POST']


@pytest.mark.parametrize('command,expected', [
    (['note', 'list'], 'notes'), (['todo', 'list'], 'tasks'),
    (['todo', 'list', '--all'], 'tasks'),
])
def test_read_notes_and_todos_does_not_write(monkeypatch, capsys, command, expected):
    def request(method, path, payload=None):
        assert method == 'GET'
        if path == '/projects':
            return [{'id': 2, 'name': 'Example'}]
        assert path == f'/projects/2/{expected}'
        return [{'id': 1, 'status': 'backlog'}, {'id': 2, 'status': 'completed'}]
    monkeypatch.setattr(notes_cli, 'request', request)
    assert notes_cli.main([*command, '--project-id', '2']) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result[expected]) == (1 if command == ['todo', 'list'] else 2)


def test_add_todo_is_scoped_and_does_not_create_note(monkeypatch, capsys):
    def request(method, path, payload=None):
        if method == 'GET':
            return [{'id': 2, 'name': 'Example'}]
        assert path == '/projects/2/tasks' and payload == {'title': 'Check backup'}
        return {'id': 7, 'project_id': 2}
    monkeypatch.setattr(notes_cli, 'request', request)
    assert notes_cli.main(['todo', 'add', '--project-id', '2', '--title', 'Check backup']) == 0
    assert json.loads(capsys.readouterr().out)['todo_id'] == 7


def test_request_uses_configured_port(tmp_path, monkeypatch):
    import io
    config = tmp_path / 'config.toml'
    config.write_text('[server]\nport = 9876\n', encoding='utf-8')
    monkeypatch.setenv('PROJECTHUB_CONFIG', str(config))
    monkeypatch.delenv('PROJECTHUB_PORT', raising=False)
    seen = []

    class Opener:
        def open(self, req, timeout):
            seen.append(req.full_url)
            return io.BytesIO(b'[]')

    monkeypatch.setattr(notes_cli, 'build_opener', lambda *handlers: Opener())
    assert notes_cli.request('GET', '/projects') == []
    assert seen == ['http://127.0.0.1:9876/api/projects']


@pytest.mark.parametrize('selector', [['--project-id', '3'], ['--project', 'Website']])
def test_session_list_reads_only_selected_project(monkeypatch, capsys, selector):
    session = {'id': 8, 'project_id': 3, 'thread_id': 'thread-8',
               'title': 'Website layout', 'is_linked': True}
    calls = []

    def request(method, path, payload=None):
        calls.append((method, path, payload))
        if path == '/projects':
            return [{'id': 3, 'name': 'Website'}, {'id': 4, 'name': 'Other'}]
        assert path == '/projects/3/codex/sessions'
        return [session]

    monkeypatch.setattr(notes_cli, 'request', request)
    assert notes_cli.main(['session', 'list', *selector]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['project_id'] == 3
    assert result['sessions'] == [session]
    assert [method for method, _, _ in calls] == ['GET', 'GET']


@pytest.mark.parametrize('explicit', [False, True])
def test_session_link_uses_current_or_explicit_thread_once(monkeypatch, capsys, explicit):
    calls = []
    monkeypatch.setenv('CODEX_THREAD_ID', 'current-thread')
    thread_id = 'selected-thread' if explicit else 'current-thread'

    def request(method, path, payload=None):
        calls.append((method, path, payload))
        if path == '/projects':
            return [{'id': 3, 'name': 'Website'}]
        assert path == '/projects/3/codex/sessions'
        if method == 'GET':
            return []
        assert method == 'POST'
        assert payload == {'thread_id': thread_id, 'title': 'Layout session'}
        return {'id': 8, 'project_id': 3, 'thread_id': thread_id,
                'title': 'Layout session', 'is_linked': True}

    monkeypatch.setattr(notes_cli, 'request', request)
    command = ['session', 'link', '--project-id', '3', '--title', ' Layout session ']
    if explicit:
        command += ['--session', thread_id]
    assert notes_cli.main(command) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {'session_id': 8, 'thread_id': thread_id, 'project_id': 3,
                      'project': 'Website', 'title': 'Layout session', 'already_linked': False}
    assert [method for method, _, _ in calls] == ['GET', 'GET', 'POST']


@pytest.mark.parametrize('linked', [False, True])
def test_session_link_is_idempotent_and_can_relink(monkeypatch, capsys, linked):
    calls = []
    session = {'id': 8, 'project_id': 3, 'thread_id': 'current-thread',
               'title': 'Existing name', 'is_linked': linked}
    monkeypatch.setenv('CODEX_THREAD_ID', 'current-thread')

    def request(method, path, payload=None):
        calls.append((method, path, payload))
        if path == '/projects':
            return [{'id': 3, 'name': 'Website'}]
        assert path == '/projects/3/codex/sessions'
        if method == 'GET':
            return [session]
        assert not linked
        assert method == 'POST' and payload == {'thread_id': 'current-thread'}
        return {**session, 'is_linked': True}

    monkeypatch.setattr(notes_cli, 'request', request)
    assert notes_cli.main(['session', 'link', '--project-id', '3']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['session_id'] == 8
    assert result['title'] == 'Existing name'
    assert result['already_linked'] is linked
    assert [method for method, _, _ in calls] == (['GET', 'GET'] if linked else ['GET', 'GET', 'POST'])


@pytest.mark.parametrize('thread_id,title', [
    ('', None), ('bad/thread', None), ('bad thread', None), ('x' * 201, None),
    ('valid-thread', ''), ('valid-thread', '   '), ('valid-thread', 'x' * 201),
])
def test_session_link_rejects_invalid_input_before_requests(monkeypatch, capsys, thread_id, title):
    def request(*args, **kwargs):
        pytest.fail('Invalid input must not access ProjectHub')

    monkeypatch.setattr(notes_cli, 'request', request)
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    command = ['session', 'link', '--project-id', '3', '--session', thread_id]
    if title is not None:
        command += ['--title', title]
    assert notes_cli.main(command) == 1
    assert capsys.readouterr().err


def test_session_link_missing_current_id_does_not_guess(monkeypatch, capsys):
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    monkeypatch.setattr(notes_cli, 'request', lambda *args, **kwargs: pytest.fail('No session ID'))
    assert notes_cli.main(['session', 'link', '--project-id', '3']) == 1
    assert 'session' in capsys.readouterr().err.lower()


def test_session_link_ambiguous_project_does_not_write(monkeypatch):
    calls = []

    def request(method, path, payload=None):
        calls.append((method, path))
        assert method == 'GET' and path == '/projects'
        return [{'id': 3, 'name': 'Website'}, {'id': 4, 'name': 'Website'}]

    monkeypatch.setattr(notes_cli, 'request', request)
    assert notes_cli.main(['session', 'link', '--project', 'Website', '--session', 'thread-1']) == 1
    assert calls == [('GET', '/projects')]


@pytest.mark.parametrize('response', [
    {'id': 8, 'project_id': 4, 'thread_id': 'thread-1', 'is_linked': True},
    {'id': 8, 'project_id': 3, 'thread_id': 'foreign-thread', 'is_linked': True},
    {'id': 8, 'project_id': 3, 'thread_id': 'thread-1', 'is_linked': False},
    {'id': True, 'project_id': 3, 'thread_id': 'thread-1', 'is_linked': True},
    {'project_id': 3, 'thread_id': 'thread-1', 'is_linked': True},
])
def test_session_link_rejects_unconfirmed_or_foreign_response(monkeypatch, capsys, response):
    posts = []

    def request(method, path, payload=None):
        if path == '/projects':
            return [{'id': 3, 'name': 'Website'}]
        if method == 'GET':
            return []
        posts.append(payload)
        return response

    monkeypatch.setattr(notes_cli, 'request', request)
    assert notes_cli.main(['session', 'link', '--project-id', '3', '--session', 'thread-1']) == 1
    assert len(posts) == 1
    output = capsys.readouterr()
    assert not output.out
    assert 'Sources' in output.err or 'session list' in output.err


def test_session_link_failed_submission_is_not_retried(monkeypatch, capsys):
    calls = []

    def request(method, path, payload=None):
        calls.append((method, path, payload))
        if path == '/projects':
            return [{'id': 3, 'name': 'Website'}]
        if method == 'GET':
            return []
        raise ValueError('Could not confirm link. Check Sources or projecthub session list before retrying.')

    monkeypatch.setattr(notes_cli, 'request', request)
    assert notes_cli.main(['session', 'link', '--project-id', '3', '--session', 'thread-1']) == 1
    assert [method for method, _, _ in calls] == ['GET', 'GET', 'POST']
    assert 'Sources' in capsys.readouterr().err


@pytest.mark.parametrize('response', [None, {}, [None], ['not a session']])
def test_session_link_invalid_session_list_fails_without_write(monkeypatch, capsys, response):
    def request(method, path, payload=None):
        assert method == 'GET'
        return [{'id': 3, 'name': 'Website'}] if path == '/projects' else response

    monkeypatch.setattr(notes_cli, 'request', request)
    assert notes_cli.main(['session', 'link', '--project-id', '3', '--session', 'thread-1']) == 1
    assert 'session list' in capsys.readouterr().err.lower()

def test_session_link_rejects_malformed_session_list(monkeypatch, capsys):
    monkeypatch.setenv('CODEX_THREAD_ID', 'current-thread')
    def request(method, path, payload=None):
        assert method == 'GET'
        return [{'id': 1, 'name': 'One'}] if path == '/projects' else [None]
    monkeypatch.setattr(notes_cli, 'request', request)
    assert notes_cli.main(['session', 'link', '--project-id', '1']) == 1
    assert 'Unexpected session list response' in capsys.readouterr().err


@pytest.mark.parametrize('action', ['complete', 'done'])
@pytest.mark.parametrize('status', ['backlog', 'completed'])
def test_complete_todo_scoped_and_idempotent(monkeypatch, capsys, action, status):
    calls = []
    def request(method, path, payload=None):
        calls.append((method, path, payload))
        if path == '/projects':
            return [{'id': 2, 'name': 'Example'}]
        item = {'id': 7, 'project_id': 2, 'status': status}
        if method == 'GET':
            assert path == '/projects/2/tasks'
            return [item]
        assert method == 'PATCH' and path == '/projects/2/tasks/7'
        assert payload == {'status': 'completed'}
        return {**item, 'status': 'completed'}
    monkeypatch.setattr(notes_cli, 'request', request)
    assert notes_cli.main(['todo', action, '--project-id', '2', '--todo-id', '7']) == 0
    assert json.loads(capsys.readouterr().out)['already_completed'] == (status == 'completed')
    assert len(calls) == (2 if status == 'completed' else 3)


def test_complete_missing_todo_never_writes(monkeypatch):
    def request(method, path, payload=None):
        assert method == 'GET'
        return [{'id': 2, 'name': 'Example'}] if path == '/projects' else []
    monkeypatch.setattr(notes_cli, 'request', request)
    assert notes_cli.main(['todo', 'complete', '--project-id', '2', '--todo-id', '99']) == 1
