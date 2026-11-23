"""Shared request admission for automatic AI worker counts."""

from contextlib import contextmanager
from pathlib import Path
import threading
import time


_INITIAL_TARGET = 2
_WAIT_INTERVAL = 0.1
_THROTTLE_COOLDOWN = 15
_MEMORY_SAMPLE_INTERVAL = 1
_MIN_AVAILABLE_BYTES = 512 * 1024**2
_PROC_MEMORY = Path('/proc/meminfo')
_PROC_CGROUP = Path('/proc/self/cgroup')
_CGROUP_ROOT = Path('/sys/fs/cgroup')


def _cgroup_memory_pressure():
    try:
        group = next((line[3:] for line in _PROC_CGROUP.read_text(errors='surrogateescape').splitlines()
                      if line.startswith('0::')), '')
    except OSError:
        return False
    relative = Path(group.lstrip('/'))
    if not group.startswith('/') or '..' in relative.parts:
        return False
    # A container can inherit a finite limit from a parent cgroup.
    path = _CGROUP_ROOT / relative
    for directory in (path, *path.parents):
        try:
            limit = (directory / 'memory.max').read_text().strip()
            if limit != 'max':
                usage = (directory / 'memory.current').read_text().strip()
                if int(limit) - int(usage) < _MIN_AVAILABLE_BYTES:
                    return True
        except (OSError, ValueError):
            pass
        if directory == _CGROUP_ROOT:
            break
    return False


def _linux_memory_pressure():
    try:
        with _PROC_MEMORY.open('rb') as source:
            for line in source:
                if line.startswith(b'MemAvailable:'):
                    if int(line.split()[1]) * 1024 < _MIN_AVAILABLE_BYTES:
                        return True
                    break
    except (OSError, ValueError, IndexError):
        pass
    return _cgroup_memory_pressure()


class AdaptiveWorkerPool:
    """Adjust live capacity in response to queued demand and reliable signals."""

    def __init__(self, *, clock=time.monotonic, memory_pressure=_linux_memory_pressure):
        self._condition = threading.Condition()
        self._clock = clock
        self._memory_pressure = memory_pressure
        self._target = _INITIAL_TARGET
        self._active = 0
        self._waiting = 0
        self._healthy = 0
        self._cooldown_until = float('-inf')
        self._next_memory_sample = float('-inf')
        self._memory_low = False

    def _refresh(self, now):
        if not self._active and not self._waiting:
            self._healthy = 0
            self._target = (_INITIAL_TARGET if now >= self._cooldown_until
                            else min(_INITIAL_TARGET, self._target))
        if now >= self._next_memory_sample:
            try:
                self._memory_low = self._memory_pressure()
            except OSError:
                self._memory_low = False
            self._next_memory_sample = now + _MEMORY_SAMPLE_INTERVAL
        if self._memory_low:
            self._healthy = 0
            self._target = 1

    def status(self, ceiling):
        with self._condition:
            self._refresh(self._clock())
            return {'target': min(ceiling, self._target), 'active': self._active,
                    'waiting': self._waiting, 'ceiling': ceiling}

    def _acquire(self, ceiling, check_cancelled):
        with self._condition:
            self._refresh(self._clock())
            self._waiting += 1
        acquired = False
        try:
            while True:
                check_cancelled()
                with self._condition:
                    self._refresh(self._clock())
                    if self._active < min(ceiling, self._target):
                        self._active += 1
                        self._waiting -= 1
                        acquired = True
                        return
                    self._condition.wait(_WAIT_INTERVAL)
        finally:
            if not acquired:
                with self._condition:
                    self._waiting -= 1
                    self._condition.notify_all()

    def _release(self, ceiling, *, success, throttled):
        with self._condition:
            now = self._clock()
            self._refresh(now)
            target = min(ceiling, self._target)
            if throttled:
                self._healthy = 0
                self._target = max(1, target // 2)
                self._cooldown_until = now + _THROTTLE_COOLDOWN
            elif (success and self._waiting and self._active + self._waiting > target and target < ceiling
                  and not self._memory_low and now >= self._cooldown_until):
                self._healthy += 1
                if self._healthy >= max(2, target):
                    self._target = target + 1
                    self._healthy = 0
            else:
                self._healthy = 0
            self._active -= 1
            self._condition.notify_all()

    @contextmanager
    def slot(self, ceiling, *, check_cancelled, is_throttle):
        if ceiling < 1:
            raise ValueError('Automatic worker ceiling must be positive.')
        self._acquire(ceiling, check_cancelled)
        success = throttled = False
        try:
            check_cancelled()
            yield
            success = True
        except Exception as exc:
            throttled = is_throttle(exc)
            raise
        finally:
            self._release(ceiling, success=success, throttled=throttled)
