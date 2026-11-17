"""Session association CLI behavior with a project-scoped mocked local API."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from scripts import notes_cli


@pytest.fixture
def api(monkeypatch):
    state = SimpleNamespace(
        calls=[], projects=[{'id': 3, 'name': 'Source'}, {'id': 4, 'name': 'Target'}],
        rows=[{'id': 8, 'project_id': 3, 'thread_id': 'thread-8',
               'title': 'Preparation discussion', 'is_linked': True}],
        result=None, error=None,
    )

    def request(method, path, payload=None):
        state.calls.append((method, path, payload))
        if (method, path) == ('GET', '/projects?compact=true'):
            return deepcopy(state.projects)
        if (method, path) == ('GET', '/projects/3/codex/sessions?include_unlinked=true'):
            return deepcopy(state.rows)
        if state.error:
            raise ValueError(state.error)
        row = deepcopy(state.rows[0])
        if (method, path) == ('PATCH', '/projects/3/codex/sessions/8/link'):
            assert payload == {'linked': False}
            result = state.result if state.result is not None else {**row, 'is_linked': False}
            if state.result is None:
                state.rows[0]['is_linked'] = False
            return deepcopy(result)
        if (method, path) == ('POST', '/projects/3/codex/sessions/8/move'):
            assert payload == {'target_project_id': 4}
            return deepcopy(state.result if state.result is not None else {
                **row, 'project_id': 4, 'is_linked': True})
        pytest.fail(f'Unexpected API call: {method} {path}')

    monkeypatch.setattr(notes_cli, 'request', request)
    return state


@pytest.mark.parametrize('selector', [['--session-id', '8'], ['--session', 'thread-8']])
def test_unlink_exact_identity_is_idempotent_and_never_deletes(api, capsys, selector):
    command = ['session', 'unlink', '--project-id', '3', *selector]
    assert notes_cli.main(command) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {'session_id': 8, 'thread_id': 'thread-8', 'project_id': 3,
                      'project': 'Source', 'title': 'Preparation discussion', 'already_unlinked': False}
    assert notes_cli.main(command) == 0
    assert json.loads(capsys.readouterr().out)['already_unlinked'] is True
    assert [call for call in api.calls if call[0] != 'GET'] == [
        ('PATCH', '/projects/3/codex/sessions/8/link', {'linked': False})]


@pytest.mark.parametrize('selector', [['--session-id', '8'], ['--session', 'thread-8']])
@pytest.mark.parametrize('target', [['--target-project-id', '4'], ['--target-project', 'Target']])
def test_move_is_one_atomic_request_with_confirmed_destination(api, capsys, selector, target):
    assert notes_cli.main(['session', 'move', '--project', 'Source', *selector, *target]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {'session_id': 8, 'thread_id': 'thread-8', 'project_id': 4,
                      'project': 'Target', 'from_project_id': 3, 'from_project': 'Source',
                      'title': 'Preparation discussion'}
    assert [call for call in api.calls if call[0] != 'GET'] == [
        ('POST', '/projects/3/codex/sessions/8/move', {'target_project_id': 4})]


@pytest.mark.parametrize('action', ['unlink', 'move'])
@pytest.mark.parametrize('rows', [[],
    [{'id': 8, 'project_id': 4, 'thread_id': 'thread-8', 'is_linked': True}],
    [{'id': 8, 'project_id': 3, 'thread_id': 'thread-8', 'is_linked': True}] * 2,
    [{'id': 8, 'project_id': 3, 'thread_id': 'thread-8', 'is_linked': 'true'}],
])
def test_missing_ambiguous_or_foreign_identity_never_mutates(api, capsys, action, rows):
    api.rows = rows
    command = ['session', action, '--project-id', '3', '--session', 'thread-8']
    if action == 'move':
        command += ['--target-project-id', '4']
    assert notes_cli.main(command) == 1
    assert not capsys.readouterr().out
    assert all(call[0] == 'GET' for call in api.calls)


@pytest.mark.parametrize('action', ['unlink', 'move'])
@pytest.mark.parametrize('changes', [
    {'id': 99}, {'project_id': 99}, {'thread_id': 'other-thread'}, {'is_linked': None},
])
def test_wrong_response_identity_is_not_reported_as_success_or_retried(api, capsys, action, changes):
    api.result = {**api.rows[0], 'project_id': 3 if action == 'unlink' else 4,
                  'is_linked': action == 'move', **changes}
    command = ['session', action, '--project-id', '3', '--session-id', '8']
    if action == 'move':
        command += ['--target-project-id', '4']
    assert notes_cli.main(command) == 1
    output = capsys.readouterr()
    assert not output.out and 'confirm' in output.err
    assert len([call for call in api.calls if call[0] != 'GET']) == 1


@pytest.mark.parametrize('command', [
    ['session', 'unlink', '--project-id', '3', '--session-id', '0'],
    ['session', 'unlink', '--project-id', '3', '--session', '../thread'],
    ['session', 'move', '--project-id', '3', '--session-id', '8', '--target-project-id', '-1'],
])
def test_invalid_selectors_fail_before_api_calls(api, capsys, command):
    assert notes_cli.main(command) == 1
    assert api.calls == []
    assert not capsys.readouterr().out


@pytest.mark.parametrize('target', ['3', '999'])
def test_move_rejects_same_or_missing_destination_without_mutation(api, capsys, target):
    assert notes_cli.main(['session', 'move', '--project-id', '3', '--session-id', '8',
                           '--target-project-id', target]) == 1
    assert not capsys.readouterr().out
    assert all(call[0] == 'GET' for call in api.calls)


def test_unlink_requires_explicit_session_even_inside_codex(monkeypatch):
    monkeypatch.setenv('CODEX_THREAD_ID', 'current-thread')
    monkeypatch.setattr(notes_cli, 'request', lambda *args: pytest.fail('No exact selector'))
    with pytest.raises(SystemExit) as exc:
        notes_cli.main(['session', 'unlink', '--project-id', '3'])
    assert exc.value.code == 2


@pytest.mark.parametrize('action', ['unlink', 'move'])
def test_uncertain_mutation_response_is_not_retried(api, capsys, action):
    api.error = 'Could not confirm the response. Check Sources before retrying.'
    command = ['session', action, '--project-id', '3', '--session-id', '8']
    if action == 'move':
        command += ['--target-project-id', '4']
    assert notes_cli.main(command) == 1
    assert not capsys.readouterr().out
    assert len([call for call in api.calls if call[0] != 'GET']) == 1


def test_list_can_include_unlinked_without_mutating(api, capsys):
    api.rows[0]['is_linked'] = False
    assert notes_cli.main(['session', 'list', '--project-id', '3', '--include-unlinked']) == 0
    assert json.loads(capsys.readouterr().out)['sessions'][0]['is_linked'] is False
    assert all(call[0] == 'GET' for call in api.calls)
