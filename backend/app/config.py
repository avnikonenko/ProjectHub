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

from .generation_profiles import get_generation_profile, list_generation_profiles


class ConfigError(ValueError):
    pass


DEFAULT_EXTRACTION_WORKERS = 0
AUTO_EXTRACTION_WORKERS_LIMIT = 6
MAX_EXTRACTION_WORKERS = 2_147_483_647
MAX_GENERATION_TOKEN_LIMIT = 2_147_483_647
SCHEME_SOURCE_CHARS_OPTIONS = (40_000, 60_000, 80_000)
DEFAULT_SCHEME_SOURCE_CHARS = SCHEME_SOURCE_CHARS_OPTIONS[0]
SCHEME_READ_BYTES_OPTIONS = (2 * 1024**2, 4 * 1024**2, 8 * 1024**2)
DEFAULT_SCHEME_READ_BYTES = SCHEME_READ_BYTES_OPTIONS[0]
SCHEME_PACKAGE_CHARS_OPTIONS = (140_000, 180_000, 220_000)
DEFAULT_SCHEME_PACKAGE_CHARS = SCHEME_PACKAGE_CHARS_OPTIONS[0]
_SCHEME_SETTINGS = {
    'scheme_source_chars': ('PROJECTHUB_SCHEME_SOURCE_CHARS', SCHEME_SOURCE_CHARS_OPTIONS),
    'scheme_read_bytes': ('PROJECTHUB_SCHEME_READ_BYTES', SCHEME_READ_BYTES_OPTIONS),
    'scheme_package_chars': ('PROJECTHUB_SCHEME_PACKAGE_CHARS', SCHEME_PACKAGE_CHARS_OPTIONS),
}


_settings_write_lock = threading.Lock()


TEMPLATE = f"""# ProjectHub settings. Environment variables override these values.
# Keep credentials out of this file and out of the repository.
[server]
port = 8766
# data_dir = "/absolute/path/to/projecthub-data"

[ai]
generation_timeout = 600
# Overall generation budget across all batches (seconds).
generation_budget = 900
# Stop at this provider-reported token total for one generation. Set 0 for unlimited.
generation_token_limit = 0
# Set 0 for Auto, or a positive number for a fixed worker count.
extraction_workers = {DEFAULT_EXTRACTION_WORKERS}
# Conversation review depth: quality (Thorough), balanced, or economy (Summary-assisted).
generation_profile = "quality"
# Total source excerpt characters available to Scheme.
scheme_source_chars = {DEFAULT_SCHEME_SOURCE_CHARS}
# Advanced Scheme limits for local file reads and the serialized evidence package.
scheme_read_bytes = {DEFAULT_SCHEME_READ_BYTES}
scheme_package_chars = {DEFAULT_SCHEME_PACKAGE_CHARS}
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
    extraction_workers: int = DEFAULT_EXTRACTION_WORKERS
    generation_profile: str = 'quality'
    scheme_source_chars: int = DEFAULT_SCHEME_SOURCE_CHARS
    scheme_read_bytes: int = DEFAULT_SCHEME_READ_BYTES
    scheme_package_chars: int = DEFAULT_SCHEME_PACKAGE_CHARS
    generation_token_limit: int = 0

    def apply_environment(self):
        workers = _extraction_worker_setting(self.extraction_workers)
        budget = _integer(self.generation_budget, 'ai.generation_budget', 30, 7200)
        token_limit = _generation_token_limit(self.generation_token_limit)
        profile = _profile(self.generation_profile)
        scheme_environment = {env: str(_scheme_preset(name, getattr(self, name)))
                              for name, (env, _) in _SCHEME_SETTINGS.items()}
        os.environ['PROJECTHUB_PORT'] = str(self.port)
        os.environ['PROJECTHUB_DATA_DIR'] = str(self.data_dir)
        os.environ['PROJECTHUB_GENERATION_BUDGET'] = str(budget)
        os.environ['PROJECTHUB_GENERATION_TOKEN_LIMIT'] = str(token_limit)
        os.environ['PROJECTHUB_GENERATION_TIMEOUT'] = str(self.generation_timeout)
        os.environ['PROJECTHUB_EXTRACTION_WORKERS'] = str(workers)
        os.environ['PROJECTHUB_GENERATION_PROFILE'] = profile
        with _settings_write_lock:
            os.environ.update(scheme_environment)
        os.environ['PROJECTHUB_HISTORY_AUTO_SYNC'] = str(self.history_auto_sync).lower()
        os.environ['PROJECTHUB_HISTORY_SYNC_INTERVAL'] = str(self.history_sync_interval)
        if self.codex_home is not None:
            os.environ['CODEX_HOME'] = str(self.codex_home)


def _absolute_path(value, name):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ConfigError(f'{name} must be an absolute path.')
    try:
        path = Path(value).expanduser()
    except RuntimeError as exc:
        raise ConfigError(f'{name} refers to an unavailable home directory.') from exc
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


def _extraction_worker_setting(value):
    return _integer(value, 'ai.extraction_workers', 0, MAX_EXTRACTION_WORKERS)


def _generation_token_limit(value):
    return _integer(value, 'ai.generation_token_limit', 0, MAX_GENERATION_TOKEN_LIMIT)


def _available_cpu_count():
    process_cpu_count = getattr(os, 'process_cpu_count', None)
    if process_cpu_count is not None and (count := process_cpu_count()):
        return count
    if hasattr(os, 'sched_getaffinity'):
        try:
            return len(os.sched_getaffinity(0))
        except OSError:
            pass
    return os.cpu_count() or 1


def resolve_extraction_workers(value):
    """Resolve Auto against CPUs usable by the process and the Auto cap."""
    selected = _extraction_worker_setting(value)
    return selected or max(1, min(_available_cpu_count(), AUTO_EXTRACTION_WORKERS_LIMIT))


def _boolean(value, name):
    if isinstance(value, str) and value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    if type(value) is not bool:
        raise ConfigError(f'{name} must be true or false.')
    return value


def _profile(value):
    if not isinstance(value, str):
        raise ConfigError('ai.generation_profile must be quality, balanced or economy.')
    try:
        return get_generation_profile(value).id
    except ValueError as exc:
        raise ConfigError('ai.generation_profile must be quality, balanced or economy.') from exc


def _scheme_preset(name, value):
    _, options = _SCHEME_SETTINGS[name]
    if isinstance(value, str) and value in {str(option) for option in options}:
        value = int(value)
    if type(value) is not int or value not in options:
        choices = ', '.join(str(option) for option in options[:-1]) + f' or {options[-1]}'
        raise ConfigError(f'ai.{name} must be {choices}.')
    return value


def scheme_inspection_limits():
    """Read excerpt characters, local read bytes and package characters together."""
    with _settings_write_lock:
        overrides = {name: os.environ.get(env) for name, (env, _) in _SCHEME_SETTINGS.items()}
        if all(value is not None for value in overrides.values()):
            return tuple(_scheme_preset(name, value) for name, value in overrides.items())
        configured = load_settings()
        return configured.scheme_source_chars, configured.scheme_read_bytes, configured.scheme_package_chars


def scheme_source_chars():
    """Read the effective excerpt budget for callers that only need this limit."""
    return scheme_inspection_limits()[0]


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
    allowed = {'server': {'port', 'data_dir'},
               'ai': {'generation_timeout', 'generation_budget', 'generation_token_limit', 'codex_home',
                      'extraction_workers', 'generation_profile', *_SCHEME_SETTINGS},
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
        generation_token_limit=_generation_token_limit(
            env.get('PROJECTHUB_GENERATION_TOKEN_LIMIT', ai.get('generation_token_limit', 0))),
        codex_home=None if home is None else _absolute_path(home, 'ai.codex_home'),
        extraction_workers=_extraction_worker_setting(
            env.get('PROJECTHUB_EXTRACTION_WORKERS', ai.get('extraction_workers', DEFAULT_EXTRACTION_WORKERS))),
        generation_profile=_profile(env.get('PROJECTHUB_GENERATION_PROFILE', ai.get('generation_profile', 'quality'))),
        history_auto_sync=_boolean(env.get('PROJECTHUB_HISTORY_AUTO_SYNC', history.get('auto_sync', True)),
                                   'history.auto_sync'),
        history_sync_interval=_integer(env.get('PROJECTHUB_HISTORY_SYNC_INTERVAL', history.get('sync_interval', 60)),
                                       'history.sync_interval', 30, 3600),
        **{name: _scheme_preset(name, env.get(key, ai.get(name, options[0])))
           for name, (key, options) in _SCHEME_SETTINGS.items()},
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


def _ai_settings_response(configured, *, workers=None, profile=None, budget=None, token_limit=None, scheme_changes=None):
    selected_workers = _extraction_worker_setting(
        os.environ.get('PROJECTHUB_EXTRACTION_WORKERS', configured.extraction_workers) if workers is None else workers)
    effective_profile = _profile(os.environ.get('PROJECTHUB_GENERATION_PROFILE', configured.generation_profile)
                                 if profile is None else profile)
    effective_budget = _integer(os.environ.get('PROJECTHUB_GENERATION_BUDGET', configured.generation_budget)
                                if budget is None else budget, 'ai.generation_budget', 30, 7200)
    effective_token_limit = _generation_token_limit(
        os.environ.get('PROJECTHUB_GENERATION_TOKEN_LIMIT', configured.generation_token_limit)
        if token_limit is None else token_limit)
    result = {'extraction_workers': resolve_extraction_workers(selected_workers),
            'configured_extraction_workers': configured.extraction_workers,
            'selected_extraction_workers': selected_workers,
            'min_extraction_workers': 0, 'max_extraction_workers': MAX_EXTRACTION_WORKERS,
            'generation_profile': effective_profile,
            'configured_generation_profile': configured.generation_profile,
            'generation_profiles': list_generation_profiles(),
            'generation_budget': effective_budget,
            'configured_generation_budget': configured.generation_budget,
            'min_generation_budget': 30, 'max_generation_budget': 7200,
            'generation_token_limit': effective_token_limit,
            'configured_generation_token_limit': configured.generation_token_limit,
            'min_generation_token_limit': 0, 'max_generation_token_limit': MAX_GENERATION_TOKEN_LIMIT}
    scheme_changes = scheme_changes or {}
    for name, (env, options) in _SCHEME_SETTINGS.items():
        value = getattr(configured, name)
        result[name] = _scheme_preset(name, scheme_changes.get(name, os.environ.get(env, value)))
        result[f'configured_{name}'] = value
        result[f'{name}_options'] = list(options)
    return result


def ai_worker_settings():
    """Expose only non-secret AI settings, without creating a config file."""
    with _settings_write_lock:
        configured_env = dict(os.environ)
        for key in ('PROJECTHUB_EXTRACTION_WORKERS', 'PROJECTHUB_GENERATION_PROFILE', 'PROJECTHUB_GENERATION_BUDGET',
                    'PROJECTHUB_GENERATION_TOKEN_LIMIT',
                    *(env for env, _ in _SCHEME_SETTINGS.values())):
            configured_env.pop(key, None)
        return _ai_settings_response(load_settings(configured_env))


def selected_extraction_workers():
    """Read the raw runtime setting, with a config fallback for direct starts."""
    workers = os.environ.get('PROJECTHUB_EXTRACTION_WORKERS')
    return _extraction_worker_setting(workers) if workers is not None else load_settings().extraction_workers


def generation_token_limit():
    """Read the stop threshold to capture at the start of the next generation."""
    limit = os.environ.get('PROJECTHUB_GENERATION_TOKEN_LIMIT')
    return _generation_token_limit(limit) if limit is not None else load_settings().generation_token_limit


def _worker_config_text(original, workers):
    """Edit one scalar while retaining comments; reject unsupported TOML layouts."""
    return _ai_config_text(original, {'extraction_workers': workers})


def _ai_config_text(original, changes):
    for name, value in changes.items():
        original = _ai_scalar_text(original, name, value)
    return original


def _ai_scalar_text(original, name, value):
    """Edit an application-owned scalar; comparison protects unsupported layouts."""
    if name not in ('extraction_workers', 'generation_profile', 'generation_budget', 'generation_token_limit',
                    *_SCHEME_SETTINGS):
        raise ConfigError('Unsupported AI setting.')
    encoded = str(value) if type(value) is int else '"' + value + '"'
    data = tomllib.loads(original)
    if not isinstance(data.get('ai', {}), dict):
        raise ConfigError('Invalid configuration section ai.')
    expected = deepcopy(data)
    expected.setdefault('ai', {})[name] = value
    header = re.search(r'''(?m)^\s*\[\s*(?:ai|"ai"|'ai')\s*\][ \t]*(?:#[^\r\n]*)?\r?$''', original)
    if header:
        next_header = re.search(r'(?m)^[ \t]*\[', original[header.end():])
        stop = header.end() + next_header.start() if next_header else len(original)
        section = original[header.end():stop]
        escaped = re.escape(name)
        key = re.compile(r'''(?m)^([ \t]*(?:''' + escaped + '|"' + escaped + '"|\'' + escaped + r'''\')[ \t]*=[ \t]*)([^\r\n]*)(\r?$)''')
        match = key.search(section)
        if match:
            value, marker, comment = match[2].partition('#')
            trailing = re.search(r'[ \t]*$', value)[0]
            replacement = match[1] + encoded + trailing + marker + comment + match[3]
            section = section[:match.start()] + replacement + section[match.end():]
        else:
            newline = '\r\n' if '\r\n' in original else '\n'
            # The header may already contain CR from CRLF; its remaining LF
            # must complete that original terminator before the inserted line.
            if section.startswith('\n'):
                section = '\n' + f'{name} = {encoded}' + newline + section[1:]
            else:
                section = newline + f'{name} = {encoded}' + newline + section
        updated = original[:header.end()] + section + original[stop:]
    else:
        updated = original.rstrip('\r\n') + f'\n\n[ai]\n{name} = {encoded}\n'
    try:
        if tomllib.loads(updated) != expected:
            raise ConfigError('This settings layout cannot be updated automatically.')
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError('This settings layout cannot be updated automatically.') from exc
    return updated


_UNSET = object()


def save_ai_worker_settings(workers):
    """Compatibility entry point for existing worker-only callers."""
    return save_ai_settings(extraction_workers=workers)


def save_ai_settings(*, extraction_workers=_UNSET, generation_profile=_UNSET, generation_budget=_UNSET,
                     generation_token_limit=_UNSET, scheme_source_chars=_UNSET, scheme_read_bytes=_UNSET,
                     scheme_package_chars=_UNSET):
    """Persist all requested scalars atomically, then update the next-run defaults."""
    changes = {}
    if extraction_workers is not _UNSET:
        changes['extraction_workers'] = _extraction_worker_setting(extraction_workers)
    if generation_profile is not _UNSET:
        changes['generation_profile'] = _profile(generation_profile)
    if generation_budget is not _UNSET:
        changes['generation_budget'] = _integer(generation_budget, 'ai.generation_budget', 30, 7200)
    if generation_token_limit is not _UNSET:
        changes['generation_token_limit'] = _generation_token_limit(generation_token_limit)
    for name, value in (('scheme_source_chars', scheme_source_chars), ('scheme_read_bytes', scheme_read_bytes),
                        ('scheme_package_chars', scheme_package_chars)):
        if value is not _UNSET:
            changes[name] = _scheme_preset(name, value)
    if not changes:
        raise ConfigError('Provide an AI setting to update.')
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
        updated = _ai_config_text(original, changes)
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
            configured = load_settings({'PROJECTHUB_CONFIG': temporary})
            # Validate unmodified runtime overrides before changing the file too.
            result = _ai_settings_response(configured, workers=changes.get('extraction_workers'),
                                           profile=changes.get('generation_profile'), budget=changes.get('generation_budget'),
                                           token_limit=changes.get('generation_token_limit'),
                                           scheme_changes=changes)
            if path.is_symlink() or (path.exists() != existed):
                raise ConfigError('Settings changed while saving; reload and try again.')
            if existed and not stat.S_ISREG(path.stat().st_mode):
                raise ConfigError('Settings changed while saving; reload and try again.')
            if existed and path.read_bytes().decode('utf-8') != original:
                raise ConfigError('Settings changed while saving; reload and try again.')
            os.replace(temporary, path)
            if 'extraction_workers' in changes:
                os.environ['PROJECTHUB_EXTRACTION_WORKERS'] = str(changes['extraction_workers'])
            if 'generation_profile' in changes:
                os.environ['PROJECTHUB_GENERATION_PROFILE'] = changes['generation_profile']
            if 'generation_budget' in changes:
                os.environ['PROJECTHUB_GENERATION_BUDGET'] = str(changes['generation_budget'])
            if 'generation_token_limit' in changes:
                os.environ['PROJECTHUB_GENERATION_TOKEN_LIMIT'] = str(changes['generation_token_limit'])
            for name, (env, _) in _SCHEME_SETTINGS.items():
                if name in changes:
                    os.environ[env] = str(changes[name])
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return result
