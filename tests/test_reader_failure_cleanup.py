"""Real local JSONL fixtures; no Codex or OpenAI requests are made."""
import json
import subprocess
import sys
import threading
import time

import pytest

from backend.app.integrations.codex import progress, protocol
from backend.app.integrations.codex.protocol import AppServerClient, CodexError


@pytest.fixture
def local_server(tmp_path, monkeypatch):
    script = tmp_path / 'failure_server.py'
    script.write_text('''import json,os,signal,sys,time
request=json.loads(sys.stdin.readline())
print(json.dumps({'id':request['id'],'result':{}}),flush=True)
sys.stdin.readline() # initialized notification
signal.signal(signal.SIGTERM,signal.SIG_IGN)
request=json.loads(sys.stdin.readline())
mode=request['method']
if mode=='malformed': print('not-json',flush=True)
elif mode=='oversized': print(json.dumps({'method':'event','params':{'text':'x'*(2*1024*1024)}}),flush=True)
elif mode=='invalid-type': print('[]',flush=True)
elif mode=='invalid-id': print(json.dumps({'id':[],'result':{}}),flush=True)
elif mode=='error-string': print(json.dumps({'id':request['id'],'error':'malformed error'}),flush=True)
elif mode=='error-null': print(json.dumps({'id':request['id'],'error':None}),flush=True)
elif mode=='error-list': print(json.dumps({'id':request['id'],'error':[]}),flush=True)
elif mode=='truncated':
 os.write(1,b'{"id":')
 os.close(1)
elif mode=='abrupt-eof': os.close(1)
elif mode=='clean-eof': sys.exit(0)
time.sleep(30)
''')
    spawn = subprocess.Popen
    monkeypatch.setattr(protocol.subprocess, 'Popen', lambda args, **kwargs:
                        spawn([sys.executable, str(script)], **kwargs))
    clients = []

    def create(on_disconnect=None):
        client = AppServerClient(executable=sys.executable, timeout=2, on_disconnect=on_disconnect)
        clients.append(client)
        return client

    yield create
    for client in clients:
        client.close()
        client.reader.join(timeout=1)


@pytest.mark.parametrize('mode', ['malformed', 'oversized', 'invalid-type', 'invalid-id',
                                 'truncated', 'abrupt-eof', 'clean-eof',
                                 'error-string', 'error-null', 'error-list'])
def test_reader_failure_releases_resources_and_notifies_once(local_server, mode):
    disconnected = []
    client = local_server(lambda: disconnected.append(True))
    start = time.monotonic()
    with pytest.raises(CodexError, match='disconnected.*unverified'):
        client.request(mode, {})
    client.reader.join(timeout=.5)
    assert time.monotonic() - start < 1.5
    assert client.closed and client.process.poll() is not None
    assert not client.reader.is_alive()
    assert client.process.stdin.closed and client.process.stdout.closed
    assert client.pending == {}
    assert disconnected == [True]
    client.close()
    assert disconnected == [True]


def test_explicit_close_does_not_report_unexpected_disconnect(local_server):
    disconnected = []
    client = local_server(lambda: disconnected.append(True))
    closers = [threading.Thread(target=client.close) for _ in range(4)]
    for closer in closers:
        closer.start()
    for closer in closers:
        closer.join(timeout=1)
        assert not closer.is_alive()
    client.reader.join(timeout=.5)
    assert disconnected == []
    assert client.closed and client.process.poll() is not None
    assert client.process.stdin.closed and client.process.stdout.closed


def test_disconnect_callback_can_close_concurrently_without_duplicate_notification(local_server):
    entered, release = threading.Event(), threading.Event()
    disconnected = []

    def callback():
        disconnected.append(True)
        entered.set()
        assert release.wait(2)

    client = local_server(callback)
    try:
        with pytest.raises(CodexError, match='disconnected'):
            client.request('malformed', {})
        assert entered.wait(1)
        closers = [threading.Thread(target=client.close) for _ in range(3)]
        for closer in closers:
            closer.start()
        for closer in closers:
            closer.join(timeout=.5)
            assert not closer.is_alive()
        assert client.closed and client.process.poll() is not None
    finally:
        release.set()
    client.reader.join(timeout=.5)
    assert disconnected == [True]


def test_failed_disconnect_callback_cannot_abandon_resources(local_server, caplog):
    def callback():
        raise RuntimeError('synthetic persistence error')

    client = local_server(callback)
    with pytest.raises(CodexError, match='disconnected'):
        client.request('malformed', {})
    client.reader.join(timeout=.5)
    assert not client.reader.is_alive()
    assert client.closed and client.process.poll() is not None
    assert client.process.stdin.closed and client.process.stdout.closed
    assert 'Codex disconnect callback failed' in caplog.text


def test_cancellation_closes_without_unexpected_disconnect_callback(local_server):
    disconnected = []
    client = local_server(lambda: disconnected.append(True))
    progress.begin(99889, 'knowledge')
    timer = threading.Timer(.1, progress.current_cancel_event().set)
    timer.start()
    try:
        with pytest.raises(progress.GenerationCancelled):
            client.request('stall', {})
    finally:
        timer.join()
        progress.finish(99889)
    client.reader.join(timeout=.5)
    assert disconnected == []
    assert client.closed and client.process.poll() is not None
    assert client.process.stdin.closed and client.process.stdout.closed


def test_request_registered_during_failure_cleanup_is_also_woken(local_server, monkeypatch):
    client = local_server()
    entered, release = threading.Event(), threading.Event()
    original_close = client.close

    def gated_close():
        if threading.current_thread() is client.reader:
            entered.set()
            assert release.wait(2)
        original_close()

    monkeypatch.setattr(client, 'close', gated_close)
    errors = []

    def request(method):
        try:
            client.request(method, {})
        except CodexError as error:
            errors.append(str(error))

    first = threading.Thread(target=request, args=('malformed',))
    second = threading.Thread(target=request, args=('another-request',))
    try:
        first.start()
        assert entered.wait(1)
        second.start()
        deadline = time.monotonic() + 1
        while len(client.pending) < 2 and time.monotonic() < deadline:
            time.sleep(.005)
        assert len(client.pending) == 2
        release.set()
        first.join(timeout=1)
        second.join(timeout=1)
        assert not first.is_alive() and not second.is_alive()
        assert len(errors) == 2 and all('disconnected' in error and 'unverified' in error for error in errors)
        assert client.pending == {}
    finally:
        release.set()
        first.join(timeout=2)
        if second.ident is not None:
            second.join(timeout=2)
