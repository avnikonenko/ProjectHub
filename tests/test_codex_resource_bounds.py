"""Real local-pipe deadlines and isolated session/cache lifecycle regressions.

The subprocess is a deliberately stalled local JSONL fixture, not Codex or an
OpenAI generation. Session tests use fake clients and temporary SQLite only.
"""
import copy
import json
import os
import subprocess
import sys
import threading
import time

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.integrations.codex import history_duplicates as duplicates, progress, protocol
from backend.app.integrations.codex.protocol import AppServerClient, CodexError
from backend.app.models import AuditEvent, CodexSession
from backend.tests.integrations.test_codex import setup
from backend.tests.integrations.test_history_duplicates import history
from backend.tests.integrations.test_protocol import fake_server


@pytest.fixture
def session_environment(setup):
    # The shared manager fixture creates ORM tables only. Add the two empty
    # migration-owned history tables used by the real unlink route's counter.
    with setup.engine.begin() as connection:
        connection.execute(text('CREATE TABLE codex_history_index (project_id INTEGER PRIMARY KEY, indexed_at TEXT, indexed_turns INTEGER)'))
        connection.execute(text('CREATE TABLE codex_history_turns (id INTEGER PRIMARY KEY, project_id INTEGER, session_id INTEGER)'))
    return setup


@pytest.fixture
def stalled_server(tmp_path, monkeypatch):
    script = tmp_path / 'server.py'
    script.write_text('''import json,signal,sys,time
m=json.loads(sys.stdin.readline())
print(json.dumps({'id':m['id'],'result':{}}),flush=True)
sys.stdin.readline() # initialized notification
signal.signal(signal.SIGTERM,signal.SIG_IGN)
time.sleep(30) # a server that never reads the next request
''')
    spawn = subprocess.Popen
    monkeypatch.setattr(protocol.subprocess, 'Popen', lambda args, **kwargs:
                        spawn([sys.executable, str(script)], **kwargs))
    client = AppServerClient(executable=sys.executable, timeout=1)
    yield client
    client.close()


def test_write_and_cleanup_are_covered_by_timeout(stalled_server):
    client = stalled_server
    client.timeout = .15
    start = time.monotonic()
    with pytest.raises(CodexError, match='timed out.*unverified'):
        client.request('turn/start', {'input': 'x' * 240_000})
    assert time.monotonic() - start < 1.5
    assert client.closed and client.process.poll() is not None
    assert client.pending == {}


def test_close_interrupts_a_blocked_write_without_taking_its_lock(stalled_server):
    client = stalled_server
    client.timeout = 20
    errors = []
    def request():
        try:
            client.request('turn/start', {'input': 'x' * 240_000})
        except CodexError as error:
            errors.append(error)
    worker = threading.Thread(target=request)
    worker.start()
    time.sleep(.1)
    start = time.monotonic()
    client.close()
    worker.join(timeout=1)
    assert not worker.is_alive() and errors
    assert time.monotonic() - start < 1.5
    assert client.process.poll() is not None


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='Linux fork/pipe resource regression')
def test_timeout_is_not_pinned_by_a_descendant_holding_stdout(tmp_path, monkeypatch):
    script = tmp_path / 'descendant.py'
    script.write_text('''import json,os,signal,sys,time
m=json.loads(sys.stdin.readline())
print(json.dumps({'id':m['id'],'result':{}}),flush=True)
sys.stdin.readline()
if os.fork()==0:
 time.sleep(2)
 os._exit(0)
signal.signal(signal.SIGTERM,signal.SIG_IGN)
time.sleep(30)
''')
    spawn = subprocess.Popen
    monkeypatch.setattr(protocol.subprocess, 'Popen', lambda args, **kwargs:
                        spawn([sys.executable, str(script)], **kwargs))
    client = AppServerClient(executable=sys.executable, timeout=1)
    client.timeout = .1
    start = time.monotonic()
    try:
        with pytest.raises(CodexError, match='timed out.*unverified'):
            client.request('turn/start', {'input': 'x' * 240_000})
        assert time.monotonic() - start < 1.0
        assert client.closed and client.process.poll() is not None
        client.reader.join(timeout=.2)
        assert not client.reader.is_alive()
        assert client.process.stdin.closed and client.process.stdout.closed
    finally:
        client.close()


def test_cancellation_reaches_a_stalled_write(stalled_server):
    client = stalled_server
    progress.begin(99881, 'knowledge')
    timer = threading.Timer(.1, progress.current_cancel_event().set)
    timer.start()
    start = time.monotonic()
    try:
        with pytest.raises(progress.GenerationCancelled):
            client.request('turn/start', {'input': 'x' * 240_000})
        assert time.monotonic() - start < 1.5
        assert client.closed and client.process.poll() is not None
    finally:
        timer.join()
        progress.finish(99881)


def test_overall_budget_is_checked_during_transport_io(stalled_server):
    client = stalled_server
    progress.begin(99882, 'knowledge')
    progress._clocks[99882] = (time.monotonic() - 1, .1)
    try:
        with pytest.raises(CodexError, match='overall time budget'):
            client.request('turn/start', {'input': 'x' * 240_000})
        assert client.closed and client.process.poll() is not None
    finally:
        progress.finish(99882)


def test_nonblocking_setup_failure_releases_the_spawned_process(fake_server, monkeypatch):
    processes = []
    spawn = protocol.subprocess.Popen
    def tracked_spawn(*args, **kwargs):
        process = spawn(*args, **kwargs)
        processes.append(process)
        return process
    def fail_setup(*args):
        raise OSError('Cannot configure pipe')
    monkeypatch.setattr(protocol.subprocess, 'Popen', tracked_spawn)
    monkeypatch.setattr(protocol.os, 'set_blocking', fail_setup)
    with pytest.raises(CodexError, match='Unable to start'):
        AppServerClient(executable=sys.executable)
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed


def test_multibyte_response_preserves_the_original_character_limit(tmp_path, monkeypatch):
    script = tmp_path / 'multibyte.py'
    script.write_text('''import json,sys
for line in sys.stdin:
 m=json.loads(line)
 if m.get('method')=='initialized':continue
 result={'text':'\\U0001f600'*600000} if m.get('method')=='multibyte' else {}
 print(json.dumps({'id':m['id'],'result':result},ensure_ascii=False),flush=True)
''')
    spawn = subprocess.Popen
    monkeypatch.setattr(protocol.subprocess, 'Popen', lambda args, **kwargs:
                        spawn([sys.executable, str(script)], **kwargs))
    client = AppServerClient(executable=sys.executable, timeout=2)
    try:
        result = client.request('multibyte', {})
        assert result['text'] == '\U0001f600' * 600_000
        frame = json.dumps({'id': 2, 'result': result}, ensure_ascii=False) + '\n'
        assert len(frame) < 2 * 1024 * 1024 < len(frame.encode('utf-8'))
    finally:
        client.close()


def test_unlink_releases_all_clients_and_preserves_relink(session_environment):
    setup = session_environment
    base = '/api/projects/1/codex'
    sessions = [setup.client.post(base + '/sessions', json={'title': str(i)}).json() for i in range(5)]
    clients = list(setup.manager.clients.values())
    assert len(clients) == 5
    for session in sessions:
        assert setup.client.patch(base + f"/sessions/{session['id']}/link", json={'linked': False}).status_code == 200
    assert setup.manager.clients == {} and all(client.closed for client in clients)
    session_id = sessions[0]['id']
    assert setup.client.patch(base + f'/sessions/{session_id}/link', json={'linked': True}).status_code == 200
    assert setup.client.post(base + f'/sessions/{session_id}/resume').status_code == 200
    assert session_id in setup.manager.clients
    with Session(setup.engine) as db:
        assert len(list(db.scalars(select(AuditEvent).where(AuditEvent.action == 'session_unlinked')))) == 5


def test_unlink_releases_original_account_without_resolving_changed_settings(session_environment):
    setup = session_environment
    base = '/api/projects/1/codex'
    sid = setup.client.post(base + '/sessions', json={'title': 'Original account'}).json()['id']
    client = setup.manager.clients.pop(sid)
    child = copy.copy(setup.manager)
    child.clients, child.account_managers = {sid: client}, {}
    setup.manager.account_managers[(1, 'removed-profile', '/removed/home')] = child
    with Session(setup.engine) as db:
        db.get(CodexSession, sid).codex_account = 'removed-profile'
        db.commit()
    assert setup.client.patch(base + f'/sessions/{sid}/link', json={'linked': False}).status_code == 200
    assert not child.clients and client.closed


def test_running_and_foreign_sessions_cannot_be_closed_by_unlink(session_environment):
    setup = session_environment
    base = '/api/projects/1/codex'
    sid = setup.client.post(base + '/sessions', json={'title': 'Keep running'}).json()['id']
    client = setup.manager.clients[sid]
    assert setup.client.patch(f'/api/projects/2/codex/sessions/{sid}/link', json={'linked': False}).status_code == 404
    with Session(setup.engine) as db:
        db.get(CodexSession, sid).status = 'running'
        db.commit()
    assert setup.client.patch(base + f'/sessions/{sid}/link', json={'linked': False}).status_code == 409
    assert not client.closed and setup.manager.clients[sid] is client


def test_prefix_cache_replaces_old_project_revisions_and_releases_unlinked(history):
    engine, _ = history
    with Session(engine) as db:
        for i in range(8):
            db.execute(text('UPDATE codex_sessions SET history_indexed_at=:v WHERE project_id=1'), {'v': str(i)})
            db.commit()
            assert duplicates.indexed_sharing(db, 1)[0].canonical
            assert len([key for key in duplicates._cache if key[0] is engine and key[1] == 1]) == 1
        db.execute(text('UPDATE codex_sessions SET is_linked=0 WHERE project_id=1'))
        db.commit()
        assert not duplicates.indexed_sharing(db, 1)[0].canonical
        assert not [key for key in duplicates._cache if key[0] is engine and key[1] == 1]


@pytest.mark.parametrize('budget', ['MAX_INDEX_TURNS', 'MAX_CACHE_SESSIONS', 'MAX_ID_CHARS'])
def test_over_budget_dedup_preserves_every_original_source(history, monkeypatch, budget):
    engine, _ = history
    monkeypatch.setattr(duplicates, budget, 1)
    sources = [{'id': str(sid), 'session_id': sid, 'turn_id': tid, 'text': 'same excerpt'}
               for sid, tid in ((1, 'a1'), (2, 'b1'))]
    original = copy.deepcopy(sources)
    with Session(engine) as db:
        assert not duplicates.indexed_sharing(db, 1)[0].canonical
        assert duplicates.deduplicate_sources(db, 1, sources) == original
        assert sources == original


def test_cache_total_turn_limit_evicts_old_entries_without_losing_current_data(history, monkeypatch):
    engine, _ = history
    monkeypatch.setattr(duplicates, 'MAX_CACHED_TURNS', 6)
    with Session(engine) as db:
        duplicates.indexed_sharing(db, 1)
        duplicates.indexed_sharing(db, 2)
        assert sum(duplicates._turn_count(value) for value in duplicates._cache.values()) <= 6
        assert duplicates.indexed_sharing(db, 1)[0].canonical
