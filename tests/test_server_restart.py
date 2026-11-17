"""Restart API/controller tests; server lifecycle is mocked here, not protocol traffic."""
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app import cli
from backend.app.integrations.codex.brief import generation_lock
from backend.app.main import create_app
from backend.app.models import CodexSession, Delivery, Project
from backend.app.restart import RestartController


@pytest.fixture
def managed(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    controller = RestartController()
    stopped = []
    controller.bind(lambda: stopped.append(True))
    app = create_app(tmp_path, restart_controller=controller)
    with TestClient(app) as client:
        yield app, client, controller, stopped
    controller.close(cancel=True)


def test_unmanaged_and_instance_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    with TestClient(create_app(tmp_path)) as client:
        state = client.get('/api/settings/restart').json()
        assert state['available'] is False and state['restarting'] is False
        assert 'projecthub command' in state['reason']
        assert client.post('/api/settings/restart', json={}).status_code == 503
        assert client.get('/api/health').json()['status'] == 'ok'
    app = create_app(tmp_path)
    assert app.state.instance_id != state['instance_id']
    app.state.engine.dispose()


def test_restart_reserves_work_and_rejects_duplicates(managed):
    app, client, controller, stopped = managed
    state = client.get('/api/settings/restart').json()
    assert state == {'available': True, 'instance_id': app.state.instance_id,
                     'restarting': False, 'reason': None}
    response = client.post('/api/settings/restart', json={})
    assert response.status_code == 202
    assert response.json() == {'status': 'restarting', 'instance_id': state['instance_id']}
    assert stopped == [True]
    assert not generation_lock.acquire(blocking=False)
    assert client.post('/api/settings/restart', json={}).status_code == 409
    assert client.post('/api/projects', json={'name': 'blocked'}).status_code == 409
    assert client.get('/api/settings/restart').json()['restarting'] is True
    assert client.get('/api/projects').json() == []
    assert client.post('/api/settings/restart', json={}, headers={'origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/settings/restart', json={}, headers={'sec-fetch-site': 'cross-site'}).status_code == 403


def test_busy_generation_and_inflight_write_release_reservation(managed):
    app, client, controller, _ = managed
    generation_lock.acquire()
    try:
        assert client.post('/api/settings/restart', json={}).status_code == 409
    finally:
        generation_lock.release()
    with controller.mutation():
        assert client.post('/api/settings/restart', json={}).status_code == 409
    assert controller.requested is False
    assert generation_lock.acquire(blocking=False)
    generation_lock.release()
    assert client.post('/api/settings/restart', json={}).status_code == 202


@pytest.mark.parametrize('busy', ['running', 'sending'])
def test_busy_codex_records(managed, busy):
    app, client, controller, _ = managed
    with Session(app.state.engine) as db:
        project = Project(name='test')
        db.add(project); db.flush()
        session = CodexSession(project_id=project.id, thread_id='thread', status='running' if busy == 'running' else 'idle')
        db.add(session); db.flush()
        if busy == 'sending':
            db.add(Delivery(project_id=project.id, session_id=session.id, content='test', status='sending'))
        db.commit()
    assert client.post('/api/settings/restart', json={}).status_code == 409
    assert not controller.requested
    assert generation_lock.acquire(blocking=False)
    generation_lock.release()


def test_concurrent_restart_acceptance_is_atomic(managed):
    app, _, controller, _ = managed
    barrier = Barrier(2)
    def reserve():
        barrier.wait()
        try:
            controller.reserve(app.state.engine)
            return 202
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: reserve(), range(2))) == [202, 409]


def test_failed_shutdown_callback_releases_reservation(managed):
    app, _, controller, _ = managed
    def fail():
        raise RuntimeError('shutdown failed')
    controller.bind(fail)
    controller.reserve(app.state.engine)
    with pytest.raises(RuntimeError):
        controller.stop()
    assert not controller.requested
    assert generation_lock.acquire(blocking=False)
    generation_lock.release()


@pytest.mark.parametrize('exec_fails', [False, True])
@pytest.mark.parametrize('packaged', [False, True])
def test_cli_restart_preserves_original_environment(tmp_path, monkeypatch, capsys, exec_fails, packaged):
    config_path = tmp_path / 'config.toml'
    config_path.write_text('[server]\nport = 8888\n[ai]\ngeneration_timeout = 120')
    monkeypatch.setenv('PROJECTHUB_CONFIG', str(config_path))
    monkeypatch.setenv('PROJECTHUB_PORT', '8889')
    monkeypatch.setenv('SECRET_TEST_VALUE', 'never-print-me')
    monkeypatch.delenv('PROJECTHUB_GENERATION_TIMEOUT', raising=False)
    original = dict(os.environ)
    calls = []
    class Config:
        def __init__(self, app_factory, **kwargs):
            self.factory = app_factory
            assert kwargs['port'] == 8889
    class Server:
        def __init__(self, config):
            self.config = config
            self.should_exit = False
        def run(self):
            # Inspect the injected controller without touching mutable app data.
            controller = self.config.factory()
            assert os.environ['PROJECTHUB_GENERATION_TIMEOUT'] == '120'
            controller.requested = True
            controller.stop()
            assert self.should_exit
    def execve(*args):
        calls.append(args)
        if exec_fails:
            raise OSError('never-print-me')
    from backend.app import main
    monkeypatch.setattr(main, 'create_app', lambda **kwargs: kwargs['restart_controller'])
    monkeypatch.setitem(sys.modules, 'uvicorn', SimpleNamespace(Config=Config, Server=Server))
    monkeypatch.setattr(cli.os, 'execve', execve)
    monkeypatch.setattr(cli, 'bundled_resources_available', lambda: packaged)
    assert cli.main(['start']) == (1 if exec_fails else 0)
    options = ['-P'] if packaged else []
    assert calls == [(sys.executable, [sys.executable, *options, '-m', 'backend.app.cli', 'start'], original)]
    output = capsys.readouterr()
    assert 'never-print-me' not in output.out + output.err


@pytest.mark.parametrize(('updated', 'status', 'message'), [
    ('[ai]\nsecret = "never-print-me"', 422, 'Invalid or unknown fields'),
    ('[server]\nport = 8890', 409, 'changed server port'),
])
def test_cli_rejects_invalid_or_changed_port_before_stopping(managed, tmp_path, monkeypatch,
                                                            updated, status, message):
    app, _, _, _ = managed
    path = tmp_path / 'settings.toml'
    path.write_text('[server]\nport = 8888')
    monkeypatch.setenv('PROJECTHUB_CONFIG', str(path))
    monkeypatch.delenv('PROJECTHUB_PORT', raising=False)
    from backend.app import main
    monkeypatch.setattr(main, 'create_app', lambda **kwargs: kwargs['restart_controller'])
    class Config:
        def __init__(self, app_factory, **kwargs):
            self.factory = app_factory
    class Server:
        def __init__(self, config):
            self.config = config
            self.should_exit = False
        def run(self):
            controller = self.config.factory()
            path.write_text(updated)
            with pytest.raises(HTTPException) as error:
                controller.reserve(app.state.engine)
            assert error.value.status_code == status
            assert message in error.value.detail
            assert 'never-print-me' not in error.value.detail
            assert not self.should_exit and not controller.requested
            assert generation_lock.acquire(blocking=False)
            generation_lock.release()
    monkeypatch.setitem(sys.modules, 'uvicorn', SimpleNamespace(Config=Config, Server=Server))
    monkeypatch.setattr(cli.os, 'execve', lambda *args: pytest.fail('Rejected restart must not exec'))
    assert cli.main(['start']) == 0


def test_cli_startup_failure_returns_uvicorn_exit_code_without_exec(tmp_path, monkeypatch):
    path = tmp_path / 'config.toml'
    path.write_text('[server]\nport = 8888')
    monkeypatch.setenv('PROJECTHUB_CONFIG', str(path))
    class Config:
        def __init__(self, app_factory, **kwargs):
            self.factory = app_factory
    class Server:
        started = False
        def __init__(self, config):
            self.config = config
        def run(self):
            pass
    monkeypatch.setitem(sys.modules, 'uvicorn', SimpleNamespace(Config=Config, Server=Server))
    monkeypatch.setattr(cli.os, 'execve', lambda *args: pytest.fail('Failed startup must not restart'))
    assert cli.main(['start']) == 3
