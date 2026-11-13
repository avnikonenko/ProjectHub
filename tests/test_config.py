import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app import cli
from backend.app.config import ConfigError, Settings, TEMPLATE, config_path, create_config, load_settings


def test_defaults_and_xdg_paths(tmp_path):
    env = {'XDG_CONFIG_HOME': str(tmp_path / 'config'), 'XDG_DATA_HOME': str(tmp_path / 'data')}
    settings = load_settings(env)
    assert config_path(env) == tmp_path / 'config/projecthub/config.toml'
    assert settings.port == 8766 and settings.generation_timeout == 600
    assert settings.data_dir == tmp_path / 'data/projecthub'
    assert settings.codex_home is None
    assert settings.extraction_workers == 2
    assert settings.history_auto_sync is True and settings.history_sync_interval == 60


def test_history_sync_config_and_overrides(tmp_path):
    path = tmp_path / 'settings.toml'
    path.write_text('[history]\nauto_sync = false\nsync_interval = 120\n')
    env = {'PROJECTHUB_CONFIG': str(path)}
    settings = load_settings(env)
    assert not settings.history_auto_sync and settings.history_sync_interval == 120
    settings = load_settings({**env, 'PROJECTHUB_HISTORY_AUTO_SYNC': 'true',
                              'PROJECTHUB_HISTORY_SYNC_INTERVAL': '90'})
    assert settings.history_auto_sync and settings.history_sync_interval == 90


def test_file_settings_and_environment_precedence(tmp_path):
    path = tmp_path / 'settings.toml'
    path.write_text(f"""
[server]
port = 8800
data_dir = "{tmp_path}/file-data"
[ai]
generation_timeout = 900
codex_home = "{tmp_path}/file-codex"
""")
    env = {'PROJECTHUB_CONFIG': str(path)}
    file_settings = load_settings(env)
    assert (file_settings.port, file_settings.generation_timeout) == (8800, 900)
    assert file_settings.codex_home == tmp_path / 'file-codex'
    overrides = {
        **env, 'PROJECTHUB_PORT': '8900', 'PROJECTHUB_DATA_DIR': str(tmp_path / 'env-data'),
        'PROJECTHUB_GENERATION_TIMEOUT': '1200', 'CODEX_HOME': str(tmp_path / 'env-codex'),
    }
    settings = load_settings(overrides)
    assert (settings.port, settings.generation_timeout) == (8900, 1200)
    assert settings.data_dir == tmp_path / 'env-data'
    assert settings.codex_home == tmp_path / 'env-codex'


@pytest.mark.parametrize('content', [
    '[server]\nport = true', '[server]\nport = 65536', '[server]\nport = 0',
    '[server]\ndata_dir = "relative"', '[ai]\ncodex_home = "relative"',
    '[ai]\ngeneration_timeout = 29', '[ai]\ngeneration_timeout = 3601',
    '[server]\npassword = "do-not-print-this"',
    '[credentials]\napi_key = "do-not-print-this"', '[server]\nport =',
    '[history]\nauto_sync = "perhaps"', '[history]\nsync_interval = 0',
])
def test_invalid_config_is_explicit_and_never_prints_values(tmp_path, content):
    path = tmp_path / 'config.toml'
    path.write_text(content)
    with pytest.raises(ConfigError) as exc:
        load_settings({'PROJECTHUB_CONFIG': str(path)})
    assert 'do-not-print-this' not in str(exc.value)


def test_missing_explicit_file_and_relative_environment_are_rejected(tmp_path):
    with pytest.raises(ConfigError, match='does not exist'):
        load_settings({'PROJECTHUB_CONFIG': str(tmp_path / 'missing.toml')})
    with pytest.raises(ConfigError, match='absolute path'):
        load_settings({'PROJECTHUB_CONFIG': 'relative.toml'})
    with pytest.raises(ConfigError, match='absolute path'):
        load_settings({'XDG_CONFIG_HOME': str(tmp_path), 'PROJECTHUB_DATA_DIR': 'relative'})
    with pytest.raises(ConfigError, match='integer'):
        load_settings({'XDG_CONFIG_HOME': str(tmp_path), 'PROJECTHUB_PORT': 'oops'})


def test_create_config_is_private_and_does_not_replace_existing_file(tmp_path):
    env = {'XDG_CONFIG_HOME': str(tmp_path)}
    path = create_config(env)
    assert path.read_text() == TEMPLATE
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    path.write_text('existing settings')
    assert create_config(env) == path
    assert path.read_text() == 'existing settings'


def test_cli_applies_settings_before_start_and_never_prints_config(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'config.toml'
    path.write_text(f'[server]\nport = 8888\ndata_dir = "{tmp_path}/data"\n[ai]\ngeneration_timeout = 120')
    monkeypatch.setenv('PROJECTHUB_CONFIG', str(path))
    for key in ('PROJECTHUB_PORT', 'PROJECTHUB_DATA_DIR', 'PROJECTHUB_GENERATION_TIMEOUT', 'PROJECTHUB_EXTRACTION_WORKERS', 'CODEX_HOME'):
        monkeypatch.delenv(key, raising=False)
    calls = []
    def run(*args, **kwargs):
        calls.append((args, kwargs, os.environ['PROJECTHUB_DATA_DIR'], os.environ['PROJECTHUB_GENERATION_TIMEOUT']))
    monkeypatch.setitem(__import__('sys').modules, 'uvicorn', SimpleNamespace(run=run))
    assert cli.main(['start']) == 0
    assert calls == [(('backend.app.main:create_app',), {
        'factory': True, 'host': '127.0.0.1', 'port': 8888,
    }, str(tmp_path / 'data'), '120')]
    assert capsys.readouterr().out.strip() == 'ProjectHub: http://127.0.0.1:8888'


def test_cli_invalid_settings_returns_error_without_echoing_contents(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'config.toml'
    path.write_text('[ai]\nsecret = "private-value"')
    monkeypatch.setenv('PROJECTHUB_CONFIG', str(path))
    assert cli.main(['start']) == 1
    output = capsys.readouterr()
    assert output.out == ''
    assert 'Invalid or unknown fields' in output.err
    assert 'private-value' not in output.err


def test_example_template_matches_created_config():
    assert Path('config.example.toml').read_text() == TEMPLATE


@pytest.mark.parametrize('workers', [1, 2, 3, 4])
def test_extraction_workers_file_setting_and_environment_override(tmp_path, workers):
    path = tmp_path / 'config.toml'
    path.write_text(f'[ai]\nextraction_workers = {workers}\n')
    env = {'PROJECTHUB_CONFIG': str(path)}
    assert load_settings(env).extraction_workers == workers
    override = 1 if workers == 4 else 4
    assert load_settings({**env, 'PROJECTHUB_EXTRACTION_WORKERS': str(override)}).extraction_workers == override


@pytest.mark.parametrize('value', ['true', 'false', '0', '5', '1.5', '"unknown"', '"do-not-print-this"'])
def test_invalid_extraction_worker_config_has_sanitized_error(tmp_path, value):
    path = tmp_path / 'config.toml'
    path.write_text(f'[ai]\nextraction_workers = {value}\n')
    with pytest.raises(ConfigError, match='ai.extraction_workers must be an integer from 1 to 4') as exc:
        load_settings({'PROJECTHUB_CONFIG': str(path)})
    assert 'do-not-print-this' not in str(exc.value)


@pytest.mark.parametrize('value', ['true', '0', '5', 'unknown', 'do-not-print-this', '1.5'])
def test_invalid_extraction_worker_environment_has_sanitized_error(tmp_path, value):
    with pytest.raises(ConfigError, match='ai.extraction_workers must be an integer from 1 to 4') as exc:
        load_settings({'XDG_CONFIG_HOME': str(tmp_path), 'PROJECTHUB_EXTRACTION_WORKERS': value})
    assert str(exc.value) == 'ai.extraction_workers must be an integer from 1 to 4.'


def test_extraction_worker_unknown_field_is_not_silently_ignored(tmp_path):
    path = tmp_path / 'config.toml'
    path.write_text('[ai]\nextraction_worker = "do-not-print-this"\n')
    with pytest.raises(ConfigError, match='Invalid or unknown fields') as exc:
        load_settings({'PROJECTHUB_CONFIG': str(path)})
    assert 'do-not-print-this' not in str(exc.value)


def test_settings_preserves_positional_fields_and_applies_validated_worker_count(tmp_path, monkeypatch):
    # Existing positional callers retain every prior field's meaning.
    settings = Settings(8888, tmp_path, 120, None, 600, False, 90)
    assert settings.extraction_workers == 2
    assert settings.generation_budget == 600
    assert settings.history_auto_sync is False and settings.history_sync_interval == 90
    path = tmp_path / 'config.toml'
    path.write_text('[ai]\nextraction_workers = 4\n')
    loaded = load_settings({'PROJECTHUB_CONFIG': str(path)})
    for name in ('PROJECTHUB_PORT', 'PROJECTHUB_DATA_DIR', 'PROJECTHUB_GENERATION_BUDGET',
                 'PROJECTHUB_GENERATION_TIMEOUT', 'PROJECTHUB_EXTRACTION_WORKERS',
                 'PROJECTHUB_HISTORY_AUTO_SYNC', 'PROJECTHUB_HISTORY_SYNC_INTERVAL'):
        monkeypatch.delenv(name, raising=False)
    loaded.apply_environment()
    assert os.environ['PROJECTHUB_EXTRACTION_WORKERS'] == '4'


@pytest.mark.parametrize('value', [True, 0, 5, 'do-not-print-this'])
def test_direct_invalid_settings_worker_value_cannot_enter_environment(tmp_path, monkeypatch, value):
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '2')
    settings = Settings(8766, tmp_path, 600, None, extraction_workers=value)
    with pytest.raises(ConfigError, match='ai.extraction_workers must be an integer from 1 to 4') as exc:
        settings.apply_environment()
    assert 'do-not-print-this' not in str(exc.value)
    assert os.environ['PROJECTHUB_EXTRACTION_WORKERS'] == '2'
