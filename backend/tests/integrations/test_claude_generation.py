import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from backend.app.integrations.codex import claude_generation, progress
from backend.app.integrations.codex.generation_metrics import GenerationMetrics, collect_generation_metrics
from backend.app.integrations.codex.protocol import CodexError


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    def install(body):
        path = tmp_path / 'claude'
        path.write_text(f'#!{sys.executable}\n' + body)
        path.chmod(0o700)
        monkeypatch.setattr(claude_generation.shutil, 'which', lambda name: str(path))
        return path

    return install


def test_claude_uses_only_supplied_data_and_validates_result(fake_cli, monkeypatch):
    monkeypatch.setenv('HOME', '/signed-in-user')
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', '/configured-claude-profile')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'fixture-key')
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'must-not-leak')
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-leak')
    monkeypatch.setenv('CLAUDECODE', 'must-not-leak')
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
assert os.environ['CLAUDE_CONFIG_DIR'] == '/configured-claude-profile'
assert os.getcwd() != os.environ['HOME']
assert not set(os.environ) & {'ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'OPENAI_API_KEY', 'CLAUDECODE'}
assert 'Selected evidence' not in args and 'fixture-key' not in args
print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                  'structured_output': {'received': sys.stdin.read(), 'cwd': os.getcwd()},
                  'usage': {'input_tokens': 10, 'output_tokens': 5,
                            'cache_read_input_tokens': 4, 'cache_creation_input_tokens': 1}}))
''')
    manager = claude_generation.ClaudeGenerationManager(None, 'opus', 'medium')
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
