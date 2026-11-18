"""Conservative background synchronization of already-associated local threads."""
from datetime import datetime, timezone
import logging
import threading
import time

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ...models import CodexSession
from .accounts import account_home
from .history_search import HistoryIndexBusy, index_history
from .manager import read_session_history
from .protocol import AppServerClient, CodexError

log = logging.getLogger(__name__)


class _SessionReader:
    """Use only the read connection opened for this exact session identity."""
    def __init__(self, session, client):
        self.identity = (session.id, session.project_id, session.thread_id, session.codex_account)
        self.client = client

    def _new_read_client(self):
        return self

    def close(self):
        # The sync pass owns the transport through its metadata recheck.
        pass

    def history(self, session, cursor, *, client, archive):
        identity = (session.id, session.project_id, session.thread_id, session.codex_account)
        if client is not self or not archive or identity != self.identity:
            raise CodexError('History synchronization session changed.')
        params = {'threadId': session.thread_id, 'itemsView': 'full', 'limit': 1, 'sortDirection': 'asc'}
        if cursor is not None:
            params['cursor'] = cursor
        return read_session_history(self.client, session, params, clip_messages=False, limit=1)


def _metadata(client, session):
    result = client.request('thread/read', {'threadId': session.thread_id, 'includeTurns': False})
    thread = result.get('thread')
    if not isinstance(thread, dict) or thread.get('id') != session.thread_id:
        raise CodexError('Unsupported saved thread metadata.')
    status = thread.get('status')
    if not isinstance(status, dict) or not isinstance(status.get('type'), str):
        raise CodexError('Unsupported saved thread status.')
    if status['type'] not in ('active', 'idle', 'notLoaded'):
        raise CodexError('Saved thread status does not permit synchronization.')
    stamp = thread.get('updatedAt')
    try:
        activity = datetime.fromtimestamp(stamp, timezone.utc).isoformat() if type(stamp) in (int, float) else None
    except (OverflowError, OSError, ValueError):
        activity = None
    return (session.thread_id, session.codex_account, activity, status['type'])


class HistorySync:
    """One local read-only worker; model work and external histories are never changed."""
    def __init__(self, engine, manager_factory, interval=60):
        self.engine = engine
        self.manager_factory = manager_factory
        self.interval = interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._pass_lock = threading.Lock()
        self._thread = None
        self._active_client = None
        self._fingerprints = {}
        self._errors = {}
        self._pass_error = False

    def start(self):
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, daemon=True, name='projecthub-history-sync')
            self._thread.start()

    def close(self):
        self._stop.set()
        with self._lock:
            client = self._active_client
            self._active_client = None
            worker = self._thread
        # Closing the transport interrupts its pending request. Do not return to
        # engine disposal while the worker could still commit an index update.
        if client is not None:
            client.close()
        if worker is not None and worker is not threading.current_thread():
            worker.join()
        with self._pass_lock:
            pass

    def read_status(self, project_id):
        with Session(self.engine) as db:
            enabled = set(db.scalars(select(CodexSession.id).where(
                CodexSession.project_id == project_id, CodexSession.auto_sync.is_(True),
                CodexSession.is_linked.is_(True))))
        with self._lock:
            failed = sorted(sid for pid, sid in self._errors if pid == project_id and sid in enabled)
            pass_error = self._pass_error and bool(enabled)
        error = 'History sync is unavailable. Previously saved history remains available.' if pass_error else None
        if failed:
            ids = ', '.join(f'#{sid}' for sid in failed[:3])
            suffix = ' and others' if len(failed) > 3 else ''
            error = f'History sync could not read session {ids}{suffix}. Previously saved history remains available. Retry the history refresh or check Sources.'
        return {'error': error}

    @staticmethod
    def _enabled_identity(session):
        return (
            CodexSession.id == session.id, CodexSession.project_id == session.project_id,
            CodexSession.auto_sync.is_(True),
            CodexSession.is_linked.is_(True),
            CodexSession.thread_id == session.thread_id,
            CodexSession.codex_account == session.codex_account,
        )

    def _session_enabled(self, session):
        with Session(self.engine) as db:
            return db.scalar(select(CodexSession.id).where(*self._enabled_identity(session))) is not None

    def _record_activity(self, session, activity):
        if activity is None or activity == session.codex_activity_at:
            return
        with Session(self.engine) as db:
            db.execute(update(CodexSession).where(
                *self._enabled_identity(session),
                CodexSession.codex_activity_at.is_distinct_from(activity),
            ).values(codex_activity_at=activity, updated_at=CodexSession.updated_at))
            db.commit()

    def _run(self):
        if self._stop.wait(5):
            return
        while not self._stop.is_set():
            try:
                self.sync_once()
            except Exception:
                # Do not expose driver errors, paths, or arbitrary server text.
                with self._lock:
                    self._pass_error = True
                log.warning('History synchronization pass failed.')
            if self._stop.wait(self.interval):
                return

    def sync_once(self):
        if self._stop.is_set() or not self._pass_lock.acquire(blocking=False):
            return
        try:
            with Session(self.engine) as db:
                sessions = list(db.scalars(select(CodexSession).where(
                    CodexSession.auto_sync.is_(True), CodexSession.is_linked.is_(True))
                    .order_by(CodexSession.project_id, CodexSession.id)))
            current = {(session.project_id, session.id) for session in sessions}
            with self._lock:
                self._errors = {key: value for key, value in self._errors.items() if key in current}
                self._pass_error = False
                self._fingerprints = {key: value for key, value in self._fingerprints.items() if key in current}
            for session in sessions:
                if self._stop.is_set():
                    break
                self._sync_session(session)
        finally:
            self._pass_lock.release()

    def _sync_session(self, session):
        key = (session.project_id, session.id)
        client = None
        try:
            if not self._session_enabled(session):
                return
            if session.codex_account is None:
                client = self.manager_factory()._new_read_client()
            else:
                client = AppServerClient(codex_home=account_home(session.codex_account, self.engine))
            with self._lock:
                self._active_client = client
            if self._stop.is_set():
                return
            fingerprint = _metadata(client, session)
            if self._stop.is_set():
                return
            self._record_activity(session, fingerprint[2])
            if fingerprint[3] == 'active':
                return
            with self._lock:
                previous = self._fingerprints.get(key)
            # Reconcile occasionally even when a coarse timestamp did not
            # change. Missing timestamps conservatively refresh every pass.
            now = time.monotonic()
            unchanged = previous is not None and previous[0] == fingerprint and now - previous[1] < 900
            if fingerprint[2] is not None and unchanged:
                with self._lock:
                    self._errors.pop(key, None)
                return
            reader = _SessionReader(session, client)
            index_history(self.engine, session.project_id, reader, session_ids={session.id}, cancelled=self._stop.is_set)
            # A thread can change while pages are being read. In that case the
            # next pass rereads it instead of treating a mixed snapshot as final.
            after = _metadata(client, session) if not self._stop.is_set() else None
            if after == fingerprint and self._session_enabled(session):
                with self._lock:
                    self._fingerprints[key] = (fingerprint, now)
            with self._lock:
                self._errors.pop(key, None)
        except HistoryIndexBusy:
            # Manual indexing owns the lock. Retry next pass without claiming a failure.
            pass
        except Exception:
            if not self._stop.is_set() and self._session_enabled(session):
                with self._lock:
                    self._errors[key] = True
                log.warning('Could not synchronize saved history for project %d session %d.', session.project_id, session.id)
        finally:
            with self._lock:
                close_client = client is not None and self._active_client is client
                if close_client:
                    self._active_client = None
            if close_client:
                client.close()
