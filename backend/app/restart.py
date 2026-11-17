"""Coordinated restart for the ProjectHub-owned Uvicorn launcher."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .models import CodexSession, Delivery


@contextmanager
def own_data_directory(directory):
    path = Path(directory).expanduser().absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise OSError('Application data directory must not be a symlink')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # Lock the directory inode for the process lifetime. A failed launcher
        # must never mark another server's active database work interrupted.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


class RestartController:
    def __init__(self, validate_restart=None):
        self._validate_restart = validate_restart
        self._lock = RLock()
        self._stop_server = None
        self._active_writes = 0
        self._generation_lock = None
        self.requested = False

    def bind(self, stop_server):
        self._stop_server = stop_server

    @property
    def available(self):
        return self._stop_server is not None

    @contextmanager
    def mutation(self):
        # Admission and restart acceptance share a lock: a request admitted
        # before acceptance must finish before restart can be accepted.
        with self._lock:
            if self.requested:
                raise HTTPException(409, 'ProjectHub is restarting. Wait for it to reconnect.')
            self._active_writes += 1
        try:
            yield
        finally:
            with self._lock:
                self._active_writes -= 1

    def reserve(self, engine):
        from .integrations.codex.brief import generation_lock
        with self._lock:
            if not self.available:
                raise HTTPException(503, 'Start ProjectHub with the projecthub command to enable restart.')
            if self.requested:
                raise HTTPException(409, 'ProjectHub is already restarting.')
            if self._active_writes or not generation_lock.acquire(blocking=False):
                raise HTTPException(409, 'Wait for active work to finish before restarting ProjectHub.')
            try:
                with Session(engine) as db:
                    running = db.scalar(select(CodexSession.id).where(CodexSession.status == 'running').limit(1))
                    sending = db.scalar(select(Delivery.id).where(Delivery.status == 'sending').limit(1))
                if running is not None or sending is not None:
                    raise HTTPException(409, 'Wait for the active Codex turn to finish before restarting ProjectHub.')
                if self._validate_restart is not None:
                    self._validate_restart()
                self._generation_lock = generation_lock
                self.requested = True
            except BaseException:
                generation_lock.release()
                raise

    def stop(self):
        try:
            self._stop_server()
        except BaseException:
            self.close(cancel=True)
            raise

    def close(self, *, cancel=False):
        with self._lock:
            if self._generation_lock is not None:
                self._generation_lock.release()
                self._generation_lock = None
            if cancel:
                self.requested = False


def install_restart(app, controller=None):
    instance_id = uuid4().hex
    app.state.instance_id = instance_id
    app.state.restart_controller = controller

    @app.get('/api/settings/restart')
    def restart_status():
        available = controller is not None and controller.available
        return {'available': available, 'instance_id': instance_id,
                'restarting': controller.requested if controller else False,
                'reason': None if available else 'Start ProjectHub with the projecthub command to enable restart.'}

    @app.post('/api/settings/restart', status_code=202)
    def restart():
        if controller is None:
            raise HTTPException(503, 'Start ProjectHub with the projecthub command to enable restart.')
        controller.reserve(app.state.engine)
        return JSONResponse({'status': 'restarting', 'instance_id': instance_id}, status_code=202,
                            background=BackgroundTask(controller.stop))
