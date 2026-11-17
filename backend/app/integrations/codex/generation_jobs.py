"""Independent generation leases with an exclusive maintenance gate.

The lock-compatible interface is reserved for restart/configuration/source changes.
Generation uses a short-lived lease for one project/view instead of serializing
all projects and all views behind that gate.
"""
import threading
import time


class _JobLease:
    def __init__(self, coordinator, key):
        self._coordinator = coordinator
        self._key = key
        self._token = object()

    def acquire(self, blocking=True, timeout=-1):
        return self._coordinator._acquire(self._key, self._token, blocking, timeout)

    def release(self):
        self._coordinator._release(self._key, self._token)

    def locked(self):
        with self._coordinator._condition:
            return self._coordinator._jobs.get(self._key) is self._token

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


class GenerationCoordinator:
    def __init__(self):
        self._condition = threading.Condition()
        self._maintenance = False
        self._jobs = {}

    def for_job(self, project_id, kind):
        if type(project_id) is not int or project_id < 1 or not isinstance(kind, str) or not kind.strip():
            raise ValueError('Generation requires a project and view.')
        return _JobLease(self, (project_id, kind))

    def _acquire(self, key, token, blocking, timeout):
        if not blocking and timeout != -1:
            raise ValueError("can't specify a timeout for a non-blocking call")
        if timeout < 0 and timeout != -1:
            raise ValueError('timeout value must be positive')
        deadline = None if timeout == -1 else time.monotonic() + timeout
        with self._condition:
            while self._maintenance or (bool(self._jobs) if key is None else key in self._jobs):
                if not blocking:
                    return False
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            if key is None:
                self._maintenance = True
            else:
                self._jobs[key] = token
            return True

    def _release(self, key, token):
        with self._condition:
            if key is None:
                if not self._maintenance:
                    raise RuntimeError('release unlocked generation maintenance gate')
                self._maintenance = False
            else:
                if self._jobs.get(key) is not token:
                    raise RuntimeError('release unowned generation job')
                del self._jobs[key]
            self._condition.notify_all()

    def acquire(self, blocking=True, timeout=-1):
        """Enter exclusive maintenance only when every generation has stopped."""
        return self._acquire(None, None, blocking, timeout)

    def release(self):
        self._release(None, None)

    def locked(self):
        with self._condition:
            return self._maintenance or bool(self._jobs)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


generation_lock = GenerationCoordinator()
