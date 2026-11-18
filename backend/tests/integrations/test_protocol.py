import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app.integrations.codex.protocol import AppServerClient, CodexError, server_command
from backend.app.integrations.codex import protocol


@pytest.fixture
def fake_server(tmp_path, monkeypatch):
    script = tmp_path / 'server.py'
    script.write_text('''import json,sys,time
for line in sys.stdin:
 m=json.loads(line)
 if 'method' not in m: continue
 method=m['method']
 if method=='initialized': continue
 if method=='disconnect': break
 if method=='timeout': time.sleep(5); continue
 if method=='oversize': print('x'*2200000,flush=True); continue
 if method=='approval':
  print(json.dumps({'id':900,'method':'item/permissions/requestApproval','params':{}}),flush=True)
 if method=='event':
  print(json.dumps({'method':'turn/completed','params':{'turn':{'id':'t','status':'completed'}}}),flush=True)
 result={'id':m['id'],'result':{'ok':True}}
 if method=='reject': result={'id':m['id'],'error':{'code':-1,'message':'not exposed'}}
 if method in ('thread/resume','thread/start','thread/turns/list') and 'testError' in m['params']:
  result={'id':m['id'],'error':m['params']['testError']}
 print(json.dumps(result),flush=True)
''')
    real_popen = subprocess.Popen
    monkeypatch.setattr(protocol.subprocess, 'Popen', lambda args, **kwargs:
                        real_popen([sys.executable, str(script)], **kwargs))
    return script


def test_handshake_and_request(fake_server):
    client = AppServerClient(executable=sys.executable, timeout=1)
    try:
        assert client.request('test', {}) == {'ok': True}
    finally:
        client.close()
    assert client.process.poll() is not None


def test_permission_requests_denied_without_blocking_responses(fake_server):
    events = []
    client = AppServerClient(executable=sys.executable, timeout=1,
                             on_event=lambda method, params: events.append((method, params)))
    try:
        assert client.request('approval', {})['ok']
        assert events == [('projecthub/approvalDenied', {'method': 'item/permissions/requestApproval'})]
    finally:
        client.close()


def test_callback_failure_does_not_lose_response(fake_server):
    def broken(*args):
        raise RuntimeError('database failed')
    client = AppServerClient(executable=sys.executable, timeout=1, on_event=broken)
    try:
        assert client.request('event', {})['ok']
    finally:
        client.close()


def test_explicit_error_is_sanitized(fake_server):
    client = AppServerClient(executable=sys.executable, timeout=1)
    try:
        with pytest.raises(CodexError, match='error -1') as err:
            client.request('reject', {})
        assert 'not exposed' not in str(err.value)
    finally:
        client.close()


def test_resume_active_writer_error_is_actionable_and_sanitized(fake_server):
    client = AppServerClient(executable=sys.executable, timeout=1)
    try:
        with pytest.raises(CodexError) as err:
            client.request('thread/resume', {'testError': {
                'code': -32600,
                'message': 'Thread private-marker already has an active writer at /private/path',
                'data': {'private': 'private-marker'}}})
        message = str(err.value)
        assert 'Another Codex app or CLI owns this session' in message
        assert 'End or unload' in message and 'create a separate session' in message
        assert 'An idle turn does not release session ownership' in message
        assert 'private-marker' not in message and '/private/path' not in message
    finally:
        client.close()


def test_missing_source_rollout_error_is_actionable_and_sanitized(fake_server):
    client = AppServerClient(executable=sys.executable, timeout=1)
    try:
        with pytest.raises(protocol.MissingSourceHistory, match='is missing from the local Codex history') as err:
            client.request('thread/turns/list', {'testError': {
                'code': -32600,
                'message': 'invalid paginated history lineage for private-marker: missing source rollout',
            }})
        assert 'unlink it in Sources' in str(err.value)
        assert 'private-marker' not in str(err.value)
    finally:
        client.close()


@pytest.mark.parametrize('method,code,message', [
    ('thread/turns/list', -32600, 'private-marker invalid request'),
    ('thread/turns/list', -1, 'invalid paginated history lineage for private-marker: missing source rollout'),
    ('thread/start', -32600, 'invalid paginated history lineage for private-marker: missing source rollout'),
    ('thread/resume', -32600, 'private-marker invalid request'),
    ('thread/resume', -32600, 'private-marker thread has an active turn'),
    ('thread/resume', -32600, None),
    ('thread/resume', -1, 'private-marker already has an active writer'),
    ('thread/start', -32600, 'private-marker already has an active writer'),
])
def test_other_errors_keep_generic_message(fake_server, method, code, message):
    client = AppServerClient(executable=sys.executable, timeout=1)
    try:
        with pytest.raises(CodexError) as err:
            client.request(method, {'testError': {'code': code, 'message': message}})
        assert str(err.value) == f'Codex rejected {method} (error {code})'
    finally:
        client.close()


def test_timeout_invalidates_connection(fake_server):
    client = AppServerClient(executable=sys.executable, timeout=.05)
    with pytest.raises(CodexError, match='unverified'):
        client.request('timeout', {})
    assert client.closed
    assert client.process.poll() is not None


@pytest.mark.parametrize('method', ['disconnect', 'oversize'])
def test_disconnect_and_bounded_input(fake_server, method):
    client = AppServerClient(executable=sys.executable, timeout=1)
    try:
        with pytest.raises(CodexError, match='disconnected'):
            client.request(method, {})
    finally:
        client.close()


def test_safe_process_arguments():
    args = server_command('/usr/bin/codex')
    assert args[:4] == ['/usr/bin/codex', 'app-server', '--listen', 'stdio://']
    assert 'mcp_servers={}' in args
    for name in ('apps', 'shell_tool', 'plugins', 'hooks', 'browser_use', 'computer_use'):
        assert f'features.{name}=false' in args


def test_billing_environment_keys_are_not_forwarded(fake_server, monkeypatch):
    for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_API_BASE'):
        monkeypatch.setenv(name, 'test-value')
    previous = protocol.subprocess.Popen
    observed = {}
    def capture(args, **kwargs):
        observed.update(kwargs['env'])
        return previous(args, **kwargs)
    monkeypatch.setattr(protocol.subprocess, 'Popen', capture)
    client = AppServerClient(executable=sys.executable, timeout=1)
    client.close()
    assert all(name not in observed for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_API_BASE'))
    assert 'model_provider="openai"' in server_command('/usr/bin/codex')


@pytest.mark.parametrize('failure', [RuntimeError('thread capacity exhausted'), OSError('resource limit')])
def test_reader_start_failure_closes_spawned_process_and_streams(fake_server, monkeypatch, failure):
    processes = []
    spawn = protocol.subprocess.Popen

    def tracked_spawn(*args, **kwargs):
        process = spawn(*args, **kwargs)
        processes.append(process)
        return process

    def fail_start(self):
        raise failure

    monkeypatch.setattr(protocol.subprocess, 'Popen', tracked_spawn)
    monkeypatch.setattr(protocol.threading.Thread, 'start', fail_start)
    with pytest.raises(CodexError, match='Unable to start the Codex reader.*Reduce the parallel thread count'):
        AppServerClient(executable=sys.executable, timeout=1)
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed
