"""Bounded JSONL client for the official Codex App Server (no auth-file access)."""
import json
import os
import queue
import shutil
import subprocess
import threading


class CodexError(RuntimeError):
    pass


# Per-process overrides never alter the user's global configuration. Discussion
# threads deliberately disable execution, connectors, browser tools and hooks.
DISABLED_FEATURES = ('apps', 'shell_tool', 'unified_exec', 'browser_use',
                     'computer_use', 'browser_use_external', 'remote_plugin', 'plugins',
                     'multi_agent', 'hooks')


def server_command(executable, disabled_mcp=()):
    args = [executable, 'app-server', '--listen', 'stdio://',
            '-c', 'mcp_servers={}', '-c', 'model_provider="openai"',
            '-c', 'web_search="disabled"',
            '-c', 'shell_environment_policy.inherit="none"']
    for feature in DISABLED_FEATURES:
        args += ['-c', f'features.{feature}=false']
    for name in disabled_mcp:
        args += ['-c', f'mcp_servers.{name}.enabled=false']
    return args


class AppServerClient:
    """One managed connection; unsolicited permission requests always fail closed."""
    def __init__(self, on_event=None, on_disconnect=None, executable=None, timeout=20, codex_home=None,
                 disabled_mcp=()):
        self.disabled_mcp = frozenset(disabled_mcp)
        self.on_event = on_event or (lambda method, params: None)
        self.on_disconnect = on_disconnect or (lambda: None)
        self.timeout = timeout
        self.pending = {}
        self.lock = threading.Lock()
        self.next_id = 0
        self.closed = False
        executable = executable or shutil.which('codex')
        if not executable:
            raise CodexError('Codex is not installed or is not on PATH')
        try:
            environment = dict(os.environ)
            if codex_home is not None:
                environment['CODEX_HOME'] = codex_home
            for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_API_BASE'):
                environment.pop(key, None)
            self.process = subprocess.Popen(server_command(executable, sorted(self.disabled_mcp)),
                                            env=environment, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                            text=True, encoding='utf-8', bufsize=1)
        except OSError as exc:
            raise CodexError('Unable to start Codex App Server') from exc
        self.reader = threading.Thread(target=self._read, daemon=True, name='projecthub-codex')
        self.reader.start()
        try:
            self.request('initialize', {'clientInfo': {'name': 'projecthub',
                         'title': 'ProjectHub', 'version': '0.1.0'}})
            self._write({'method': 'initialized', 'params': {}})
        except Exception:
            self.close()
            raise

    def _write(self, message):
        with self.lock:
            if self.closed or self.process.poll() is not None:
                raise CodexError('Codex App Server connection is closed')
            try:
                self.process.stdin.write(json.dumps(message, ensure_ascii=False) + '\n')
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise CodexError('Codex App Server connection was interrupted') from exc

    def request(self, method, params):
        response = queue.Queue(maxsize=1)
        with self.lock:
            self.next_id += 1
            request_id = self.next_id
            self.pending[request_id] = response
        try:
            self._write({'id': request_id, 'method': method, 'params': params})
            try:
                result = response.get(timeout=self.timeout)
            except queue.Empty as exc:
                self.close()
                raise CodexError('Codex request timed out; delivery may be unverified') from exc
            if 'error' in result:
                # Do not expose arbitrary server/config/auth details to browser clients.
                code = result['error'].get('code', 'unknown')
                message = result['error'].get('message')
                if (method == 'thread/resume' and code == -32600 and
                        isinstance(message, str) and 'already has an active writer' in message):
                    raise CodexError('Another Codex app or CLI owns this session. '
                                     'End or unload the owning session before retrying, '
                                     'or create a separate session in ProjectHub. '
                                     'An idle turn does not release session ownership.')
                raise CodexError(f'Codex rejected {method} (error {code})')
            return result.get('result', {})
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(2 * 1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 2 * 1024 * 1024:
                    break
                message = json.loads(line)
                if 'id' in message and 'method' not in message:
                    with self.lock:
                        target = self.pending.get(message['id'])
                    if target is not None:
                        target.put_nowait(message)
                elif 'id' in message:
                    # A read-only client never grants server-initiated approvals,
                    # dynamic tools, login flows or permission expansion.
                    self._write({'id': message['id'], 'error': {
                        'code': -32601, 'message': 'ProjectHub does not permit this operation'}})
                    self._event('projecthub/approvalDenied', {'method': message.get('method')})
                else:
                    self._event(message.get('method', ''), message.get('params', {}))
        except (ValueError, OSError, CodexError, queue.Full):
            pass
        finally:
            with self.lock:
                pending = list(self.pending.values())
            for response in pending:
                try:
                    response.put_nowait({'error': {'code': 'disconnected'}})
                except queue.Full:
                    pass
            if not self.closed:
                self.on_disconnect()

    def _event(self, method, params):
        try:
            self.on_event(method, params)
        except Exception:
            # A broken persistence callback must not abandon pending responses.
            import logging
            logging.getLogger(__name__).exception('Codex event callback failed')

    def close(self):
        self.closed = True
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()
