import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from backend.app.integrations.codex import claude_generation, progress
from backend.app.integrations.codex.generation_metrics import GenerationMetrics, collect_generation_metrics
from backend.app.integrations.codex.protocol import CodexError, JSONFormattingError
from backend.app.integrations.codex.manager import TransientGenerationError, request_knowledge_json


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    def install(body):
        path = tmp_path / 'claude'
        path.write_text(f'#!{sys.executable}\n' + body)
        path.chmod(0o700)
        monkeypatch.setattr(claude_generation.shutil, 'which', lambda name: str(path))
        return path

    return install


@pytest.mark.parametrize('directory', [None, '/selected-claude-profile'])
def test_claude_uses_only_supplied_data_and_validates_result(fake_cli, monkeypatch, directory):
    monkeypatch.setenv('HOME', '/signed-in-user')
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', '/configured-claude-profile')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'fixture-key')
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'must-not-leak')
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-leak')
    monkeypatch.setenv('CLAUDECODE', 'must-not-leak')
    expected_directory = directory or '/configured-claude-profile'
    fake_cli('''import json, os, sys
args = sys.argv[1:]
assert '--bare' not in args and '--no-session-persistence' in args
assert '--safe-mode' in args and '--restricted' in args
assert '--strict-mcp-config' in args and '--disable-slash-commands' in args
assert '--no-chrome' in args
assert args[args.index('--tools') + 1] == ''
assert args[args.index('--disallowedTools') + 1] == 'mcp__*'
assert args[args.index('--permission-mode') + 1] == 'dontAsk'
assert args[args.index('--permission-prompts') + 1] == 'none'
assert args[args.index('--model') + 1] == 'opus'
assert args[args.index('--effort') + 1] == 'medium'
assert args[args.index('--setting-sources') + 1] == ''
assert json.loads(args[args.index('--settings') + 1]) == {'disableAllHooks': True}
assert json.loads(args[args.index('--mcp-config') + 1]) == {'mcpServers': {}}
assert json.loads(args[args.index('--json-schema') + 1]) == {'type': 'object', 'additionalProperties': False}
assert os.environ['HOME'] == '/signed-in-user'
assert os.environ['CLAUDE_CONFIG_DIR'] == ''' + repr(expected_directory) + '''
assert os.getcwd() != os.environ['HOME']
assert not set(os.environ) & {'ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'OPENAI_API_KEY', 'CLAUDECODE'}
assert 'Selected evidence' not in args and 'fixture-key' not in args
print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                  'structured_output': {'received': sys.stdin.read(), 'cwd': os.getcwd()},
                  'usage': {'input_tokens': 10, 'output_tokens': 5,
                            'cache_read_input_tokens': 4, 'cache_creation_input_tokens': 1}}))
''')
    manager = claude_generation.ClaudeGenerationManager(None, 'opus', 'medium', directory=directory)
    metrics = GenerationMetrics()
    evidence = 'Selected evidence' * 16_000  # Requires multiple stdin writes.
    with collect_generation_metrics(metrics):
        result = json.loads(manager.generate_brief(evidence, {'type': 'object', 'additionalProperties': False},
                                                   verify_connectors=True))
    assert result['received'] == evidence
    assert not Path(result['cwd']).exists()
    assert manager.generation_metadata['provider'] == 'claude'
    assert metrics.snapshot()['model_requests'] == 1
    assert metrics.snapshot()['token_usage']['total_tokens'] == 20
    assert metrics.snapshot()['token_usage']['cached_input_tokens'] == 4


def test_claude_keeps_unsupported_bounds_in_the_prompt_for_existing_validators(fake_cli):
    fake_cli('''import json, sys
schema = json.loads(sys.argv[sys.argv.index('--json-schema') + 1])
print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                  'structured_output': {'schema': schema, 'prompt': sys.stdin.read()}}))
''')
    schema = {'type': 'object', 'properties': {
        'maximum': {'type': 'integer', 'minimum': 1, 'maximum': 3},
        'names': {'type': 'array', 'maxItems': 3, 'minItems': 1,
                  'items': {'type': 'string', 'maxLength': 20}},
    }}
    manager = claude_generation.ClaudeGenerationManager(None)
    result = json.loads(manager.generate_brief('evidence', schema))
    assert result['schema']['properties'] == {
        'maximum': {'type': 'integer'},
        'names': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}},
    }
    assert '"maximum":3' in result['prompt'] and result['prompt'].endswith('evidence')
    assert schema['properties']['maximum']['maximum'] == 3


@pytest.mark.parametrize('payload', [
    '{unparseable',
    '{"type":"result","subtype":"success","is_error":true,"structured_output":{}}',
    '{"type":"result","subtype":"success","is_error":false,"result":"{}"}',
    '{"type":"result","subtype":"success","is_error":false,"structured_output":{"value":NaN}}',
])
def test_claude_rejects_unverified_results(fake_cli, payload):
    fake_cli(f'print({payload!r})\n')
    with pytest.raises(CodexError, match='structured'):
        claude_generation.ClaudeGenerationManager(None).generate_brief('fixture', {'type': 'object'})


def test_claude_redacts_failure_output(fake_cli):
    fake_cli('import sys\nprint("private stdout")\nprint("private stderr", file=sys.stderr)\nsys.exit(1)\n')
    with pytest.raises(CodexError, match='generation failed') as caught:
        claude_generation.ClaudeGenerationManager(None).generate_brief('fixture', {'type': 'object'})
    assert 'private' not in str(caught.value)
    assert caught.value.provider_diagnostics['exit_status'] == 1
    assert caught.value.provider_diagnostics['category'] == 'unknown_exit'
    assert 'private' not in repr(caught.value.provider_diagnostics)


@pytest.mark.parametrize(('exit_code', 'stream', 'subtype', 'error'), [
    (0, 'stdout', 'error_max_structured_output_retries', JSONFormattingError),
    (1, 'stdout', 'error_max_structured_output_retries', JSONFormattingError),
    (1, 'stderr', 'error_max_structured_output_retries', JSONFormattingError),
    (1, 'both', 'error_max_structured_output_retries', JSONFormattingError),
    (0, 'stdout', 'unrecognized_failure', CodexError),
])
def test_claude_records_one_usage_report_from_failed_result(fake_cli, exit_code, stream, subtype, error):
    payload = {'type': 'result', 'subtype': subtype, 'is_error': True,
               'usage': {'input_tokens': 30, 'output_tokens': 5, 'cache_read_input_tokens': 7,
                         'cache_creation_input_tokens': 3}, 'result': 'private provider output'}
    secondary = {**payload, 'usage': {**payload['usage'], 'output_tokens': 10}}
    commands = ['import sys', 'sys.stdin.read()']
    if stream in ('stdout', 'both'):
        commands.append(f'print({json.dumps(payload)!r})')
    if stream in ('stderr', 'both'):
        envelope = secondary if stream == 'both' else payload
        commands.append(f'print({json.dumps(envelope)!r}, file=sys.stderr)')
    commands.append(f'sys.exit({exit_code})')
    fake_cli('\n'.join(commands) + '\n')
    metrics = GenerationMetrics()
    with collect_generation_metrics(metrics), pytest.raises(error) as caught:
        claude_generation.ClaudeGenerationManager(None).generate_brief('fixture', {'type': 'object'})
    assert type(caught.value) is error
    assert 'private' not in str(caught.value) + repr(metrics.snapshot())
    assert metrics.live_usage() == {'total_tokens': 45, 'reported_requests': 1, 'model_requests': 1}
    assert metrics.snapshot()['token_usage']['cached_input_tokens'] == 7


def test_claude_ignores_usage_not_attached_to_a_cli_result(fake_cli):
    payload = {'type': 'error', 'error': {'type': 'authentication_error'},
               'usage': {'input_tokens': 30, 'output_tokens': 5}}
    fake_cli(f'import sys\nprint({json.dumps(payload)!r})\nsys.exit(1)\n')
    metrics = GenerationMetrics()
    with collect_generation_metrics(metrics), pytest.raises(CodexError) as caught:
        claude_generation.ClaudeGenerationManager(None).generate_brief('fixture', {'type': 'object'})
    assert caught.value.provider_diagnostics['category'] == 'authorization_denied'
    assert metrics.live_usage() == {'total_tokens': None, 'reported_requests': 0, 'model_requests': 1}


@pytest.mark.parametrize('message,limit,reset', [
    ("You've hit your weekly limit · resets Nov 23, 1pm (Europe/Prague)",
     'weekly', 'Nov 23, 1pm (Europe/Prague)'),
    ("You've hit your 5-hour limit · resets 1:10pm (Europe/Prague)",
     '5-hour', '1:10pm (Europe/Prague)'),
    ("You've hit your limit · resets 1pm (UTC)", None, '1pm (UTC)'),
    ("You've hit your weekly limit · resets private account data", 'weekly', None),
    ("You've hit your weekly limit · resets 1pm (private)", 'weekly', '1pm'),
    ("You've hit your 5-hour limit · resets 1pm <private-script>", '5-hour', None),
])
def test_claude_exposes_only_safe_verified_usage_reset(fake_cli, message, limit, reset):
    payload = {'type': 'result', 'is_error': True, 'subtype': 'success',
               'stop_reason': 'stop_sequence', 'result': message}
    fake_cli(f'import sys\nprint({json.dumps(payload)!r})\nsys.exit(1)\n')
    with pytest.raises(CodexError) as caught:
        claude_generation.ClaudeGenerationManager(None).generate_brief('fixture', {'type': 'object'})
    failure = caught.value
    assert not isinstance(failure, (JSONFormattingError, TransientGenerationError))
    assert failure.provider_diagnostics['category'] == 'rate_limited'
    assert failure.provider_diagnostics.get('limit_kind') == limit
    assert failure.provider_diagnostics.get('reset_at') == reset
    if reset:
        assert f'resets {reset}' in str(failure)
    if limit:
        assert f'Claude {limit} limit' in str(failure)
    assert 'private' not in str(failure) + repr(failure.provider_diagnostics)


def test_claude_does_not_take_reset_details_from_unverified_or_generic_error():
    for payload in (
        {'type': 'error', 'status': 429,
         'result': "You've hit your weekly limit · resets Nov 23, 1pm (Europe/Prague)"},
        {'type': 'result', 'is_error': False, 'subtype': 'success', 'stop_reason': 'stop_sequence',
         'result': "You've hit your weekly limit · resets Nov 23, 1pm (Europe/Prague)"},
    ):
        failure = claude_generation._generation_failure(1, json.dumps(payload).encode(), b'', 0)
        assert 'reset_at' not in failure.provider_diagnostics
        assert 'limit_kind' not in failure.provider_diagnostics
        assert 'Nov 23' not in str(failure)


@pytest.mark.parametrize('payload,transient,category', [
    ({'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'private API key'}},
     True, 'service_unavailable'),
    ({'type': 'error', 'error': {'type': 'api_error', 'status_code': 503, 'message': 'private path'}},
     True, 'service_unavailable'),
    ({'type': 'result', 'is_error': True, 'error': {'type': 'network_error', 'message': 'private host'}},
     True, 'connection_failure'),
    ({'type': 'error', 'error': {'type': 'authentication_error', 'message': 'private token'}},
     False, 'authorization_denied'),
    ({'type': 'error', 'status': 429, 'error': {'type': 'api_error', 'message': 'private account'}},
     False, 'rate_limited'),
    ({'type': 'result', 'is_error': True, 'subtype': 'success', 'stop_reason': 'stop_sequence',
      'result': "You've hit your 5-hour limit · resets 1:10pm (private zone)"}, False, 'rate_limited'),
    ({'type': 'result', 'is_error': False, 'subtype': 'success', 'stop_reason': 'stop_sequence',
      'result': "You've hit your 5-hour limit · resets 1:10pm (private zone)"}, False, 'unknown_exit'),
    ({'type': 'result', 'is_error': True, 'subtype': 'success', 'stop_reason': 'stop_sequence',
      'result': 'Unrecognized private account error'}, False, 'unknown_exit'),
    ({'type': 'error', 'status': 503, 'error': {'type': 'authentication_error'}},
     False, 'authorization_denied'),
    ({'type': 'result', 'is_error': True, 'subtype': 'error_max_structured_output_retries',
      'errors': ['private schema diagnostic']}, False, 'structured_output_failure'),
    ({'type': 'result', 'is_error': False, 'structured_output': {
        'type': 'error', 'error': {'type': 'overloaded_error'}}}, False, 'unknown_exit'),
])
def test_claude_failure_uses_only_allowlisted_machine_diagnostics(fake_cli, payload, transient, category):
    fake_cli(f'import sys\nprint({json.dumps(payload)!r})\nsys.exit(1)\n')
    with pytest.raises(CodexError) as caught:
        claude_generation.ClaudeGenerationManager(None).generate_brief('private input', {'type': 'object'})
    assert isinstance(caught.value, TransientGenerationError) is transient
    assert isinstance(caught.value, JSONFormattingError) is (category == 'structured_output_failure')
    assert caught.value.provider_diagnostics['category'] == category
    assert 'exit status 1' in str(caught.value)
    assert 'private' not in str(caught.value) + repr(caught.value.provider_diagnostics)
    if category == 'rate_limited':
        assert 'Retry after the Claude usage limit resets' in str(caught.value)
        assert 'sign-in' not in str(caught.value)


def test_claude_drains_bounded_stderr_without_exposing_it(fake_cli, monkeypatch):
    monkeypatch.setattr(claude_generation, '_MAX_ERROR_BYTES', 128)
    fake_cli('import sys\nsys.stderr.write("private stderr " * 20000)\nsys.stderr.flush()\nsys.exit(7)\n')
    with pytest.raises(CodexError) as caught:
        claude_generation.ClaudeGenerationManager(None).generate_brief('fixture', {'type': 'object'})
    diagnostics = caught.value.provider_diagnostics
    assert diagnostics['exit_status'] == 7 and diagnostics['stderr_truncated'] is True
    assert diagnostics['stderr_bytes'] == len('private stderr ' * 20000)
    assert diagnostics['stdout_bytes'] == 0
    assert 'private' not in str(caught.value) + repr(diagnostics)


def test_claude_stderr_is_drained_while_writing_large_input(fake_cli):
    fake_cli('''import json, sys
sys.stderr.write('private progress ' * 20000)
sys.stderr.flush()
content = sys.stdin.read()
print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                  'structured_output': {'characters': len(content)}}))
''')
    result = claude_generation.ClaudeGenerationManager(None).generate_brief(
        'x' * 200000, {'type': 'object', 'additionalProperties': False})
    assert json.loads(result)['characters'] == 200000


def test_claude_auth_diagnostic_dominates_temporary_error_and_signals_are_not_retried():
    temporary = json.dumps({'type': 'error', 'error': {'type': 'overloaded_error'}}).encode()
    denied = json.dumps({'type': 'error', 'error': {'type': 'authentication_error'}}).encode()
    failure = claude_generation._generation_failure(1, temporary, denied, len(denied))
    assert not isinstance(failure, TransientGenerationError)
    assert failure.provider_diagnostics['category'] == 'authorization_denied'
    killed = claude_generation._generation_failure(-9, temporary, b'', 0)
    assert not isinstance(killed, TransientGenerationError)
    assert 'signal 9' in str(killed)


def test_claude_explicit_temporary_failure_uses_existing_single_retry(fake_cli, tmp_path):
    counter = tmp_path / 'attempt-count'
    fake_cli('''import json, pathlib, sys
counter = pathlib.Path(''' + repr(str(counter)) + ''')
attempt = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(attempt))
if attempt == 1:
 print(json.dumps({'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'private'}}), file=sys.stderr)
 sys.exit(1)
print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                  'structured_output': {'value': 'complete'}}))
''')
    retries = []
    result = request_knowledge_json(claude_generation.ClaudeGenerationManager(None), 'fixture',
                                    {'type': 'object'}, on_transient_retry=lambda: retries.append(True))
    assert json.loads(result) == {'value': 'complete'}
    assert counter.read_text() == '2' and retries == [True]


@pytest.mark.parametrize('exit_status', [0, 1])
def test_claude_structured_exhaustion_uses_bounded_formatting_retry(fake_cli, tmp_path, exit_status):
    counter = tmp_path / 'formatting-attempt-count'
    fake_cli('''import json, pathlib, sys
counter = pathlib.Path(''' + repr(str(counter)) + ''')
attempt = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(attempt))
print(json.dumps({'type': 'result', 'is_error': True,
 'subtype': 'error_max_structured_output_retries', 'errors': ['private diagnostic']}))
sys.exit(''' + str(exit_status) + ''')
''')
    retries = []
    with pytest.raises(JSONFormattingError) as caught:
        request_knowledge_json(claude_generation.ClaudeGenerationManager(None), 'private input',
                               {'type': 'object'}, on_retry=lambda: retries.append(True))
    assert counter.read_text() == '2' and retries == [True]
    assert caught.value.provider_diagnostics['category'] == 'structured_output_failure'
    assert caught.value.provider_diagnostics['exit_status'] == exit_status
    assert 'private' not in str(caught.value) + repr(caught.value.provider_diagnostics)


@pytest.mark.parametrize('payload,category', [
    ({'type': 'error', 'error': {'type': 'authentication_error'}}, 'authorization_denied'),
    ({'type': 'result', 'is_error': True, 'subtype': 'success', 'stop_reason': 'stop_sequence',
      'result': "You've hit your 5-hour limit · resets 1pm (private zone)"}, 'rate_limited'),
    ({'type': 'result', 'is_error': True, 'subtype': 'error_max_budget_usd'}, 'budget_limit'),
])
def test_claude_known_zero_exit_failures_remain_fatal_without_retry(fake_cli, tmp_path, payload, category):
    counter = tmp_path / 'fatal-attempt-count'
    fake_cli('import pathlib\ncounter = pathlib.Path(' + repr(str(counter)) + ')\n'
             'counter.write_text(counter.read_text() + "x" if counter.exists() else "x")\n'
             + f'print({json.dumps(payload)!r})\n')
    with pytest.raises(CodexError) as caught:
        request_knowledge_json(claude_generation.ClaudeGenerationManager(None), 'fixture', {'type': 'object'})
    assert not isinstance(caught.value, (JSONFormattingError, TransientGenerationError))
    assert counter.read_text() == 'x'
    assert caught.value.provider_diagnostics['category'] == category
    assert caught.value.provider_diagnostics['exit_status'] == 0
    assert 'private' not in str(caught.value) + repr(caught.value.provider_diagnostics)


def test_claude_formatting_classification_requires_failed_result_and_preserves_fatal_errors():
    exhausted = json.dumps({'type': 'result', 'is_error': True,
                            'subtype': 'error_max_structured_output_retries'}).encode()
    for fatal_type in ('authentication_error', 'rate_limit_error', 'invalid_request_error'):
        fatal = json.dumps({'type': 'error', 'error': {'type': fatal_type}}).encode()
        failure = claude_generation._generation_failure(1, exhausted, fatal, len(fatal))
        assert not isinstance(failure, (JSONFormattingError, TransientGenerationError))
        assert failure.provider_diagnostics['category'] != 'structured_output_failure'
    assert not isinstance(claude_generation._generation_failure(-9, exhausted, b'', 0), JSONFormattingError)
    for envelope in ({'type': 'result', 'is_error': False}, {'type': 'error'}):
        envelope['subtype'] = 'error_max_structured_output_retries'
        failure = claude_generation._generation_failure(1, json.dumps(envelope).encode(), b'', 0)
        assert not isinstance(failure, JSONFormattingError)


def test_claude_caps_output_and_stops_its_cli(fake_cli, monkeypatch):
    fake_cli('import time\nprint("x" * 1000, flush=True)\ntime.sleep(20)\n')
    monkeypatch.setattr(claude_generation, '_MAX_OUTPUT_BYTES', 256)
    with pytest.raises(CodexError, match='response limit'):
        claude_generation.ClaudeGenerationManager(None).generate_brief('fixture', {'type': 'object'})


def test_claude_checks_timeout_and_cancellation(fake_cli, monkeypatch):
    fake_cli('import time\ntime.sleep(20)\n')
    monkeypatch.setattr(claude_generation, 'generation_timeout', lambda: 0.05)
    manager = claude_generation.ClaudeGenerationManager(None)
    with pytest.raises(CodexError, match='model-call timeout'):
        manager.generate_brief('fixture', {'type': 'object'})
    event = threading.Event()
    event.set()
    with progress.abort_on(event), pytest.raises(CodexError, match='Another extraction batch failed'):
        manager.generate_brief('fixture', {'type': 'object'})


def test_claude_reuses_only_the_scoped_history_reader_and_validates_options():
    history = SimpleNamespace(history=lambda: 'project history', generate_brief=lambda: 'codex', resume=lambda: 'resume')
    manager = claude_generation.ClaudeGenerationManager(history)
    assert manager.generation_model == 'sonnet'
    assert manager.history() == 'project history'
    assert not hasattr(manager, 'resume')
    with manager.independent_generation() as worker:
        assert worker is not manager and worker.generation_metadata == manager.generation_metadata
        assert worker.history() == 'project history'
    with pytest.raises(CodexError, match='supported Claude model'):
        manager.for_generation('codex')
    with pytest.raises(CodexError, match='reasoning effort'):
        manager.for_generation('haiku', 'high')


def test_claude_missing_cli_reports_installation_error(monkeypatch):
    monkeypatch.setattr(claude_generation.shutil, 'which', lambda _: None)
    with pytest.raises(CodexError, match='Install its CLI'):
        claude_generation.ClaudeGenerationManager(None).generate_brief('fixture', {'type': 'object'})


def test_claude_verifies_account_and_copies_only_hashed_identity(fake_cli):
    directory = '/selected-claude-profile'

    def select(email, method='claude.ai'):
        payload = {'loggedIn': True, 'apiProvider': 'firstParty', 'authMethod': method, 'email': email}
        fake_cli(f'import os, sys\nassert sys.argv[-2:] == ["auth", "status"]\n'
                 f'assert os.environ["CLAUDE_CONFIG_DIR"] == {directory!r}\nprint({json.dumps(payload)!r})\n')
        return claude_generation.ClaudeGenerationManager(None, directory=directory).verify_account()

    first = select('fixture-one@example.invalid')
    second = select('fixture-two@example.invalid')
    assert first.generation_account != second.generation_account
    assert 'example.invalid' not in first.generation_account
    with first.independent_generation() as worker:
        assert worker.generation_account == first.generation_account
        assert worker._directory == directory
    with pytest.raises(CodexError, match='requires a signed-in account'):
        select('fixture-one@example.invalid', 'api_key')
