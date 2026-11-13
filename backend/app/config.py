"""User-editable local settings, without copying or reading credentials."""
from dataclasses import dataclass
from copy import deepcopy
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import tomllib
from typing import Mapping


class ConfigError(ValueError):
    pass


_settings_write_lock = threading.Lock()


TEMPLATE = """# ProjectHub settings. Environment variables override these values.
# Keep credentials out of this file and out of the repository.
[server]
port = 8766
# data_dir = "/absolute/path/to/projecthub-data"

[ai]
generation_timeout = 600
# Overall generation budget across all batches (seconds).
generation_budget = 900
# Independent extraction requests; increase only after measuring account capacity.
extraction_workers = 4
# Select an existing Codex sign-in directory; ProjectHub never copies credentials.
# codex_home = "/absolute/path/to/existing/codex-home"

[history]
auto_sync = true
sync_interval = 60
"""


@dataclass(frozen=True)
class Settings:
    port: int
    data_dir: Path
    generation_timeout: int
    codex_home: Path | None
    generation_budget: int = 900
    history_auto_sync: bool = True
    history_sync_interval: int = 60
    extraction_workers: int = 4

    def apply_environment(self):
        workers = _integer(self.extraction_workers, 'ai.extraction_workers', 1, 4)
        os.environ['PROJECTHUB_PORT'] = str(self.port)
        os.environ['PROJECTHUB_DATA_DIR'] = str(self.data_dir)
        os.environ['PROJECTHUB_GENERATION_BUDGET'] = str(self.generation_budget)
        os.environ['PROJECTHUB_GENERATION_TIMEOUT'] = str(self.generation_timeout)
        os.environ['PROJECTHUB_EXTRACTION_WORKERS'] = str(workers)
        os.environ['PROJECTHUB_HISTORY_AUTO_SYNC'] = str(self.history_auto_sync).lower()
        os.environ['PROJECTHUB_HISTORY_SYNC_INTERVAL'] = str(self.history_sync_interval)
        if self.codex_home is not None:
            os.environ['CODEX_HOME'] = str(self.codex_home)


def _absolute_path(value, name):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ConfigError(f'{name} must be an absolute path.')
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ConfigError(f'{name} must be an absolute path.')
    return path


def config_path(environ: Mapping[str, str] | None = None):
    env = os.environ if environ is None else environ
    if 'PROJECTHUB_CONFIG' in env:
        return _absolute_path(env['PROJECTHUB_CONFIG'], 'PROJECTHUB_CONFIG')
    root = _absolute_path(env.get('XDG_CONFIG_HOME', str(Path.home() / '.config')), 'XDG_CONFIG_HOME')
    return root / 'projecthub' / 'config.toml'


def _integer(value, name, minimum, maximum):
    if isinstance(value, str) and len(value) <= 10 and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ConfigError(f'{name} must be an integer from {minimum} to {maximum}.')
    return value


def _boolean(value, name):
    if isinstance(value, str) and value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    if type(value) is not bool:
        raise ConfigError(f'{name} must be true or false.')
    return value


def load_settings(environ: Mapping[str, str] | None = None):
    env = os.environ if environ is None else environ
    path = config_path(env)
    try:
        with path.open('rb') as source:
            data = tomllib.load(source)
    except FileNotFoundError as exc:
        if 'PROJECTHUB_CONFIG' in env:
            raise ConfigError('PROJECTHUB_CONFIG does not exist. Run projecthub config first.') from exc
        data = {}
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError('Could not read ProjectHub configuration. Check its permissions and TOML syntax.') from exc
    allowed = {'server': {'port', 'data_dir'}, 'ai': {'generation_timeout', 'generation_budget', 'codex_home', 'extraction_workers'},
               'history': {'auto_sync', 'sync_interval'}}
    if set(data) - allowed.keys():
        raise ConfigError('Unknown configuration section. Allowed sections: server, ai, history.')
    for section, fields in allowed.items():
        values = data.get(section, {})
        if not isinstance(values, dict) or set(values) - fields:
            raise ConfigError(f'Invalid or unknown fields in configuration section {section}.')
    server, ai = data.get('server', {}), data.get('ai', {})
    history = data.get('history', {})
    data_default = str(Path(env.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'projecthub')
    home = env.get('CODEX_HOME', ai.get('codex_home'))
    return Settings(
        port=_integer(env.get('PROJECTHUB_PORT', server.get('port', 8766)), 'server.port', 1, 65535),
        data_dir=_absolute_path(env.get('PROJECTHUB_DATA_DIR', server.get('data_dir', data_default)), 'server.data_dir'),
        generation_timeout=_integer(
            env.get('PROJECTHUB_GENERATION_TIMEOUT', ai.get('generation_timeout', 600)),
            'ai.generation_timeout', 30, 3600),
        generation_budget=_integer(env.get('PROJECTHUB_GENERATION_BUDGET', ai.get('generation_budget', 900)),
                                   'ai.generation_budget', 30, 7200),
        codex_home=None if home is None else _absolute_path(home, 'ai.codex_home'),
        extraction_workers=_integer(env.get('PROJECTHUB_EXTRACTION_WORKERS', ai.get('extraction_workers', 4)),
                                    'ai.extraction_workers', 1, 4),
        history_auto_sync=_boolean(env.get('PROJECTHUB_HISTORY_AUTO_SYNC', history.get('auto_sync', True)),
                                   'history.auto_sync'),
        history_sync_interval=_integer(env.get('PROJECTHUB_HISTORY_SYNC_INTERVAL', history.get('sync_interval', 60)),
                                       'history.sync_interval', 30, 3600),
    )


def create_config(environ: Mapping[str, str] | None = None):
    path = config_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path
    with os.fdopen(fd, 'w', encoding='utf-8') as target:
        target.write(TEMPLATE)
    return path


def ai_worker_settings():
    """Expose only non-secret worker settings, without creating a config file."""
    with _settings_write_lock:
        configured_env = dict(os.environ)
        configured_env.pop('PROJECTHUB_EXTRACTION_WORKERS', None)
        configured = load_settings(configured_env).extraction_workers
        effective = _integer(os.environ.get('PROJECTHUB_EXTRACTION_WORKERS', configured),
                             'ai.extraction_workers', 1, 4)
        return {'extraction_workers': effective, 'configured_extraction_workers': configured,
                'min_extraction_workers': 1, 'max_extraction_workers': 4}


def _worker_config_text(original, workers):
    """Edit one scalar while retaining comments; reject unsupported TOML layouts."""
    data = tomllib.loads(original)
    if not isinstance(data.get('ai', {}), dict):
        raise ConfigError('Invalid configuration section ai.')
    expected = deepcopy(data)
    expected.setdefault('ai', {})['extraction_workers'] = workers
    header = re.search(r'''(?m)^\s*\[\s*(?:ai|"ai"|'ai')\s*\][ \t]*(?:#[^\r\n]*)?\r?$''', original)
    if header:
        next_header = re.search(r'(?m)^[ \t]*\[', original[header.end():])
        stop = header.end() + next_header.start() if next_header else len(original)
        section = original[header.end():stop]
        key = re.compile(r'''(?m)^([ \t]*(?:extraction_workers|"extraction_workers"|'extraction_workers')[ \t]*=[ \t]*)([^\r\n]*)(\r?$)''')
        match = key.search(section)
        if match:
            value, marker, comment = match[2].partition('#')
            trailing = re.search(r'[ \t]*$', value)[0]
            replacement = match[1] + str(workers) + trailing + marker + comment + match[3]
            section = section[:match.start()] + replacement + section[match.end():]
        else:
            newline = '\r\n' if '\r\n' in original else '\n'
            # The header may already contain CR from CRLF; its remaining LF
            # must complete that original terminator before the inserted line.
            if section.startswith('\n'):
                section = '\n' + f'extraction_workers = {workers}' + newline + section[1:]
            else:
                section = newline + f'extraction_workers = {workers}' + newline + section
        updated = original[:header.end()] + section + original[stop:]
    else:
        updated = original.rstrip('\r\n') + f'\n\n[ai]\nextraction_workers = {workers}\n'
    try:
        if tomllib.loads(updated) != expected:
            raise ConfigError('This settings layout cannot be updated automatically.')
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError('This settings layout cannot be updated automatically.') from exc
    return updated


def save_ai_worker_settings(workers):
    """Persist one ProjectHub setting, then apply it to future generation pools."""
    workers = _integer(workers, 'ai.extraction_workers', 1, 4)
    with _settings_write_lock:
        path = config_path()
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ConfigError('Settings cannot be changed through a symbolic link.')
        existed = path.exists()
        if existed:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o222:
                raise ConfigError('Settings must be a writable regular file.')
            original = path.read_bytes().decode('utf-8')
        else:
            original = TEMPLATE
        # A new file uses the trusted template; unrelated existing data is retained.
        updated = _worker_config_text(original, workers)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ConfigError('Settings cannot be changed through a symbolic link.')
        fd, temporary = tempfile.mkstemp(prefix='.projecthub-settings-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as output:
                output.write(original)
                output.flush()
                load_settings({'PROJECTHUB_CONFIG': temporary})
                output.seek(0)
                output.truncate()
                output.write(updated)
                output.flush()
                os.fsync(output.fileno())
            # The complete resulting file must be valid before atomic replacement.
            load_settings({'PROJECTHUB_CONFIG': temporary})
            if path.is_symlink() or (path.exists() != existed):
                raise ConfigError('Settings changed while saving; reload and try again.')
            if existed and not stat.S_ISREG(path.stat().st_mode):
                raise ConfigError('Settings changed while saving; reload and try again.')
            if existed and path.read_bytes().decode('utf-8') != original:
                raise ConfigError('Settings changed while saving; reload and try again.')
            os.replace(temporary, path)
            os.environ['PROJECTHUB_EXTRACTION_WORKERS'] = str(workers)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {'extraction_workers': workers, 'configured_extraction_workers': workers,
                'min_extraction_workers': 1, 'max_extraction_workers': 4}
