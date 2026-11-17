"""Invalid local configuration must produce actionable errors, without HTTP."""
from pathlib import Path

import pytest

from backend.app import config, cli
from scripts import notes_cli


def test_unavailable_home_is_reported_as_configuration_error(monkeypatch, tmp_path, capsys):
    original = Path.expanduser
    def expand(path):
        if str(path) == '~unavailable-profile':
            raise RuntimeError('Could not determine home directory.')
        return original(path)
    monkeypatch.setattr(Path, 'expanduser', expand)
    monkeypatch.setenv('PROJECTHUB_CONFIG', str(tmp_path / 'settings.toml'))
    (tmp_path / 'settings.toml').write_text('[ai]\ncodex_home = "~unavailable-profile"\n')
    monkeypatch.delenv('CODEX_HOME', raising=False)
    with pytest.raises(config.ConfigError, match='ai.codex_home.*unavailable home'):
        config.load_settings()
    assert cli.main(['start']) == 1
    assert 'unavailable home' in capsys.readouterr().err


@pytest.mark.parametrize('setting,expected', [
    ('generation_profile = "unsupported"', 'generation_profile'),
    ('generation_budget = 0', 'generation_budget'),
])
def test_terminal_read_preserves_actual_config_failure(tmp_path, monkeypatch, capsys, setting, expected):
    path = tmp_path / 'settings.toml'
    path.write_text('[ai]\n' + setting + '\n')
    monkeypatch.setenv('PROJECTHUB_CONFIG', str(path))
    monkeypatch.delenv('PROJECTHUB_GENERATION_PROFILE', raising=False)
    monkeypatch.delenv('PROJECTHUB_GENERATION_BUDGET', raising=False)
    def no_network(*args, **kwargs):
        pytest.fail('Invalid settings must stop before an HTTP request.')
    monkeypatch.setattr(notes_cli, 'build_opener', no_network)
    assert notes_cli.main(['projects']) == 1
    error = capsys.readouterr().err
    assert expected in error
    assert 'valid local port' not in error
