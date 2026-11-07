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
