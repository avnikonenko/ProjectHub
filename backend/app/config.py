"""User-editable local settings, without copying or reading credentials."""
from dataclasses import dataclass
import os
from pathlib import Path
import tomllib
from typing import Mapping


class ConfigError(ValueError):
    pass


TEMPLATE = """# ProjectHub settings. Environment variables override these values.
# Keep credentials out of this file and out of the repository.
[server]
port = 8766
# data_dir = "/absolute/path/to/projecthub-data"

[ai]
generation_timeout = 600
# Select an existing Codex sign-in directory; ProjectHub never copies credentials.
# codex_home = "/absolute/path/to/existing/codex-home"
"""


@dataclass(frozen=True)
class Settings:
    port: int
    data_dir: Path
    generation_timeout: int
    codex_home: Path | None

    def apply_environment(self):
        os.environ['PROJECTHUB_PORT'] = str(self.port)
        os.environ['PROJECTHUB_DATA_DIR'] = str(self.data_dir)
        os.environ['PROJECTHUB_GENERATION_TIMEOUT'] = str(self.generation_timeout)
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
    allowed = {'server': {'port', 'data_dir'}, 'ai': {'generation_timeout', 'codex_home'}}
    if set(data) - allowed.keys():
        raise ConfigError('Unknown configuration section. Allowed sections: server, ai.')
    for section, fields in allowed.items():
        values = data.get(section, {})
        if not isinstance(values, dict) or set(values) - fields:
            raise ConfigError(f'Invalid or unknown fields in configuration section {section}.')
    server, ai = data.get('server', {}), data.get('ai', {})
    data_default = str(Path(env.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'projecthub')
    home = env.get('CODEX_HOME', ai.get('codex_home'))
    return Settings(
        port=_integer(env.get('PROJECTHUB_PORT', server.get('port', 8766)), 'server.port', 1, 65535),
        data_dir=_absolute_path(env.get('PROJECTHUB_DATA_DIR', server.get('data_dir', data_default)), 'server.data_dir'),
        generation_timeout=_integer(
            env.get('PROJECTHUB_GENERATION_TIMEOUT', ai.get('generation_timeout', 600)),
            'ai.generation_timeout', 30, 3600),
        codex_home=None if home is None else _absolute_path(home, 'ai.codex_home'),
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
