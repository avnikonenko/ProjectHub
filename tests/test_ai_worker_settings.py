"""Global worker controls persist only ProjectHub settings; all state is isolated."""
from pathlib import Path
import os
import tomllib

import pytest
from fastapi.testclient import TestClient

from backend.app import config
from backend.app.main import create_app
from backend.app.integrations.codex.manager import CodexManager


@pytest.fixture
def settings_client(tmp_path, monkeypatch):
    path = tmp_path / 'config' / 'projecthub' / 'config.toml'
    path.parent.mkdir(parents=True)
    path.write_text('# Preserve this comment.\n[server]\nport = 8766 # local port\n'
                    '[ai]\ngeneration_timeout = 600\nextraction_workers = 2 # measured separately\n'
                    '[history]\nauto_sync = false\nsync_interval = 120\n')
    monkeypatch.setenv('PROJECTHUB_CONFIG', str(path))
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    monkeypatch.delenv('PROJECTHUB_EXTRACTION_WORKERS', raising=False)
    monkeypatch.setattr(CodexManager, '_new_client', lambda *args, **kwargs: pytest.fail('Settings invoked Codex'))
    app = create_app(tmp_path / 'data')
    with TestClient(app) as client:
        yield client, path
    app.state.engine.dispose()


def test_read_is_global_non_mutating_and_shows_effective_override(settings_client, monkeypatch):
    client, path = settings_client
    original = path.read_bytes()
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '12')
    assert client.get('/api/settings/ai').json() == {
        'extraction_workers': 12, 'configured_extraction_workers': 2,
        'min_extraction_workers': 1, 'max_extraction_workers': config.MAX_EXTRACTION_WORKERS,
    }
    assert path.read_bytes() == original
    assert client.get('/api/projects').json() == []


def test_patch_persists_one_field_and_applies_to_future_requests(settings_client):
    client, path = settings_client
    before = tomllib.loads(path.read_text())
    response = client.patch('/api/settings/ai', json={'extraction_workers': 3})
    assert response.status_code == 200, response.text
    assert response.json()['extraction_workers'] == response.json()['configured_extraction_workers'] == 3
    after = tomllib.loads(path.read_text())
    before['ai']['extraction_workers'] = 3
    assert after == before
    assert '# Preserve this comment.' in path.read_text()
    assert 'extraction_workers = 3 # measured separately' in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    assert os.environ['PROJECTHUB_EXTRACTION_WORKERS'] == '3'
    assert config.load_settings({'PROJECTHUB_CONFIG': str(path)}).extraction_workers == 3
    assert client.get('/api/settings/ai').json()['extraction_workers'] == 3


@pytest.mark.parametrize('body', [
    {'extraction_workers': 0}, {'extraction_workers': config.MAX_EXTRACTION_WORKERS + 1}, {'extraction_workers': True},
    {'extraction_workers': '3'}, {'extraction_workers': 2.0}, {},
    {'extraction_workers': 2, 'another_setting': 'not allowed'},
])
def test_invalid_requests_preserve_file_and_runtime(settings_client, body):
    client, path = settings_client
    original = path.read_bytes()
    before = os.environ.get('PROJECTHUB_EXTRACTION_WORKERS')
    assert client.patch('/api/settings/ai', json=body).status_code == 422
    assert path.read_bytes() == original
    assert os.environ.get('PROJECTHUB_EXTRACTION_WORKERS') == before


def test_atomic_write_failure_does_not_change_runtime_or_file(settings_client, monkeypatch):
    client, path = settings_client
    original = path.read_bytes()
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '2')
    def denied(*args):
        raise PermissionError('Sensitive path must not escape')
    monkeypatch.setattr(config.os, 'replace', denied)
    response = client.patch('/api/settings/ai', json={'extraction_workers': 4})
    assert response.status_code == 503
    assert 'Sensitive path' not in response.text
    assert path.read_bytes() == original
    assert os.environ['PROJECTHUB_EXTRACTION_WORKERS'] == '2'
    assert not list(path.parent.glob('.projecthub-settings-*'))


@pytest.mark.parametrize('content', [
    '[ai]\nunknown = "do-not-expose-this"\n', '[ai]\nextraction_workers = ["do-not-expose-this"]\n',
    '[ai\nsecret = "do-not-expose-this"\n',
    'ai = "do-not-expose-this"\n', '[[ai]]\nextraction_workers = 2\n',
])
def test_invalid_configuration_is_never_replaced_or_exposed(settings_client, content):
    client, path = settings_client
    path.write_text(content)
    response = client.patch('/api/settings/ai', json={'extraction_workers': 4})
    assert response.status_code == 422
    assert 'do-not-expose-this' not in response.text
    assert path.read_text() == content
    response = client.get('/api/settings/ai')
    assert response.status_code == 422
    assert 'do-not-expose-this' not in response.text


def test_symlink_file_or_parent_is_not_followed_for_mutations(settings_client, tmp_path, monkeypatch):
    client, path = settings_client
    original = path.read_bytes()
    link = tmp_path / 'linked.toml'
    link.symlink_to(path)
    monkeypatch.setenv('PROJECTHUB_CONFIG', str(link))
    assert client.patch('/api/settings/ai', json={'extraction_workers': 4}).status_code == 422
    parent = tmp_path / 'linked-parent'
    parent.symlink_to(path.parent, target_is_directory=True)
    monkeypatch.setenv('PROJECTHUB_CONFIG', str(parent / path.name))
    assert client.patch('/api/settings/ai', json={'extraction_workers': 4}).status_code == 422
    assert path.read_bytes() == original


def test_missing_default_config_is_created_privately_only_on_save(settings_client, tmp_path, monkeypatch):
    client, _ = settings_client
    monkeypatch.delenv('PROJECTHUB_CONFIG')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'fresh-config'))
    path = config.config_path()
    response = client.get('/api/settings/ai')
    assert response.status_code == 200
    assert response.json()['extraction_workers'] == 4
    assert response.json()['configured_extraction_workers'] == 4
    assert not path.exists()
    assert client.patch('/api/settings/ai', json={'extraction_workers': 4}).status_code == 200
    assert config.load_settings().extraction_workers == 4
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('original', [
    '["ai"]\n"extraction_workers" = 2 # quoted keys\n[history]\nauto_sync = false\n',
    '[server]\nport = 8766\n',
    '[ai]\ngeneration_timeout = 600\n',
    '[ai]\r\nextraction_workers = 2 # CRLF\r\n',
    '[ai]\r\ngeneration_timeout = 600\r\n',
])
def test_supported_layouts_preserve_all_other_fields(settings_client, original):
    client, path = settings_client
    path.write_bytes(original.encode())
    response = client.patch('/api/settings/ai', json={'extraction_workers': 4})
    assert response.status_code == 200, response.text
    expected = tomllib.loads(original)
    expected.setdefault('ai', {})['extraction_workers'] = 4
    assert tomllib.loads(path.read_bytes().decode()) == expected
    if '\r\n' in original:
        assert b'\r\n' in path.read_bytes()


@pytest.mark.parametrize('content', [
    'ai = { extraction_workers = 2, generation_timeout = 600 }\n',
    'ai.extraction_workers = 2\n',
])
def test_unsupported_layout_fails_safely_without_rewriting(settings_client, content):
    client, path = settings_client
    path.write_text(content)
    response = client.patch('/api/settings/ai', json={'extraction_workers': 4})
    assert response.status_code == 422
    assert path.read_text() == content


def test_cross_origin_setting_change_is_denied(settings_client):
    client, path = settings_client
    original = path.read_bytes()
    response = client.patch('/api/settings/ai', json={'extraction_workers': 4},
                            headers={'Origin': 'https://example.com'})
    assert response.status_code == 403
    assert path.read_bytes() == original


@pytest.mark.parametrize('workers', [5, 12, config.MAX_EXTRACTION_WORKERS])
def test_worker_counts_above_four_persist_without_invoking_codex(settings_client, workers):
    client, path = settings_client
    response = client.patch('/api/settings/ai', json={'extraction_workers': workers})
    assert response.status_code == 200, response.text
    assert response.json() == {
        'extraction_workers': workers, 'configured_extraction_workers': workers,
        'min_extraction_workers': 1, 'max_extraction_workers': config.MAX_EXTRACTION_WORKERS,
    }
    assert tomllib.loads(path.read_text())['ai']['extraction_workers'] == workers
    assert client.get('/api/settings/ai').json() == response.json()
