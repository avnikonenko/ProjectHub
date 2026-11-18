"""Bounded JSONL client for the official Codex App Server (no auth-file access)."""
import json
import os
import queue
import select
import shutil
import subprocess
import threading
import time
from ...version import VERSION


class CodexError(RuntimeError):
    pass


class JSONFormattingError(CodexError):
    """Generation stopped after excessive formatting whitespace in JSON output."""


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
        self._write_lock = threading.RLock()
        self._read_lock = threading.RLock()
        self._close_lock = threading.Lock()
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
            # Use bounded nonblocking writes; a server that stops reading must
            # not trap the caller before its response timeout even starts.
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
        except OSError as exc:
            if hasattr(self, 'process'):
                self.close()
            raise CodexError('Unable to start Codex App Server') from exc
        try:
            try:
                self.reader = threading.Thread(target=self._read, daemon=True, name='projecthub-codex')
                self.reader.start()
            except (RuntimeError, OSError) as exc:
                raise CodexError('Unable to start the Codex reader. Reduce the parallel thread count and retry.') from exc
            self.request('initialize', {'clientInfo': {'name': 'projecthub',
                         'title': 'ProjectHub', 'version': VERSION}})
            self._write({'method': 'initialized', 'params': {}})
        except Exception:
            self.close()
            raise

    def _check_io(self, deadline, *, connection=True):
        from . import progress
        try:
            progress.check_cancelled()
        except CodexError:
            self.close()
            raise
        if time.monotonic() >= deadline:
            self.close()
            raise CodexError('Codex request timed out; delivery may be unverified')
        if connection and (self.closed or self.process.poll() is not None):
            raise CodexError('Codex App Server connection is closed')

    def _write(self, message, *, deadline=None):
        deadline = time.monotonic() + self.timeout if deadline is None else deadline
        # State and write locks are separate, so close/read can wake pending
        # callers while a pipe is full. Only one JSONL frame writes at a time.
        while not self._write_lock.acquire(timeout=min(.05, max(0, deadline - time.monotonic()))):
            self._check_io(deadline)
        try:
            self._check_io(deadline)
            if self.closed or self.process.poll() is not None:
                raise CodexError('Codex App Server connection is closed')
            try:
                encoded = (json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8')
                pending = memoryview(encoded)
                descriptor = self.process.stdin.fileno()
                while pending:
                    self._check_io(deadline)
                    try:
                        written = os.write(descriptor, pending)
                    except BlockingIOError:
                        select.select([], [descriptor], [], min(.05, max(0, deadline - time.monotonic())))
                        continue
                    if not written:
                        raise OSError('Closed pipe')
                    pending = pending[written:]
            except (OSError, ValueError) as exc:
                raise CodexError('Codex App Server connection was interrupted') from exc
        finally:
            self._write_lock.release()

    @property
    def connected(self):
        return not self.closed and self.process.poll() is None and self.reader.is_alive()

    def request(self, method, params):
        deadline = time.monotonic() + self.timeout
        response = queue.Queue(maxsize=1)
        with self.lock:
            self.next_id += 1
            request_id = self.next_id
            self.pending[request_id] = response
        try:
            self._write({'id': request_id, 'method': method, 'params': params}, deadline=deadline)
            while True:
                self._check_io(deadline, connection=False)
                try:
                    result = response.get(timeout=min(.05, max(0, deadline - time.monotonic())))
                    break
                except queue.Empty:
                    continue
            if 'error' in result:
                # Do not expose arbitrary server/config/auth details to browser clients.
                code = result['error'].get('code', 'unknown')
                message = result['error'].get('message')
                if code == 'disconnected':
                    raise CodexError('Codex disconnected; delivery may be unverified')
                if (method == 'thread/turns/list' and code == -32600 and isinstance(message, str)
                        and message.startswith('invalid paginated history lineage for ')
                        and message.endswith(': missing source rollout')):
                    raise CodexError('Codex cannot read this session because the original session history '
                                     'it depends on is missing from the local Codex history. '
                                     'If you no longer need this session in the project, unlink it in Sources.')
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
        buffered = bytearray()
        maximum_chars = 2 * 1024 * 1024
        try:
            with self._read_lock:
                if self.closed:
                    return
                descriptor = self.process.stdout.fileno()
            while not self.closed:
                readable, _, _ = select.select([descriptor], [], [], .05)
                if not readable:
                    continue
                # Never hold a buffered-stream read lock while waiting for an
                # inherited pipe to close. Cleanup synchronizes only this short
                # nonblocking read, so a server descendant cannot pin close().
                with self._read_lock:
                    if self.closed:
                        break
                    try:
                        chunk = os.read(descriptor, 65536)
                    except BlockingIOError:
                        continue
                if not chunk:
                    break
                buffered.extend(chunk)
                # UTF-8 needs at most four bytes per character. Preserve the
                # prior character bound and also bound unfinished wire frames.
                while (newline := buffered.find(b'\n')) >= 0:
                    line = bytes(buffered[:newline]).decode('utf-8')
                    del buffered[:newline + 1]
                    if len(line) + 1 > maximum_chars:
                        raise ValueError('Oversized Codex frame')
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError('Unsupported Codex frame')
                    if 'id' in message and 'method' not in message:
                        if 'error' in message and not isinstance(message['error'], dict):
                            raise ValueError('Unsupported Codex error envelope')
                        with self.lock:
                            target = self.pending.get(message['id'])
                        if target is not None:
                            target.put_nowait(message)
                    elif 'id' in message:
                        # No server-initiated approvals, tools or login flows.
                        self._write({'id': message['id'], 'error': {
                            'code': -32601, 'message': 'ProjectHub does not permit this operation'}})
                        self._event('projecthub/approvalDenied', {'method': message.get('method')})
                    else:
                        self._event(message.get('method', ''), message.get('params', {}))
                if len(buffered) > maximum_chars * 4:
                    break
        except (ValueError, TypeError, RecursionError, OSError, CodexError, queue.Full):
            pass
        finally:
            with self.lock:
                unexpected = not self.closed
            # EOF or invalid frames end the transport, not just its reader.
            # Release the process/pipes before pending callers can mistake a
            # dead reader for an open connection. close() is bounded and does
            # not wait on buffered reader locks, including from this thread.
            self.close()
            with self.lock:
                pending = list(self.pending.values())
            for response in pending:
                try:
                    response.put_nowait({'error': {'code': 'disconnected'}})
                except queue.Full:
                    pass
            if unexpected:
                try:
                    self.on_disconnect()
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('Codex disconnect callback failed')

    def _event(self, method, params):
        try:
            self.on_event(method, params)
        except Exception:
            # A broken persistence callback must not abandon pending responses.
            import logging
            logging.getLogger(__name__).exception('Codex event callback failed')

    def close(self):
        # Another close already owns process cleanup; callers can stop waiting
        # as soon as closed is set, without contending with a blocked writer.
        if not self._close_lock.acquire(blocking=False):
            return
        try:
            self.closed = True
            if self.process.poll() is None:
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    self.process.wait(timeout=.2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            # Termination/closed wakes writers first. Only close descriptors
            # after their writer exits, preventing a descriptor-reuse race.
            # Reentrant because the deadline checker can close its own writer.
            with self._write_lock:
                with self._read_lock:
                    for stream in (self.process.stdin, self.process.stdout):
                        if stream:
                            stream.close()
        finally:
            self._close_lock.release()
