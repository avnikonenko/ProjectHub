"""Profile controls are strict, atomic, private and independent of AI generation."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import tomllib

import pytest

from backend.app import config
from backend.app.generation_profiles import get_generation_profile, list_generation_profiles
from tests.test_ai_worker_settings import settings_client


def test_profile_contract_is_immutable_and_returns_detached_descriptors(monkeypatch):
    monkeypatch.delenv('PROJECTHUB_GENERATION_PROFILE', raising=False)
    quality = get_generation_profile()
    assert (quality.id, quality.window_chars, quality.source_budget_chars, quality.default_effort) == (
        'quality', 14_000, None, None)
    with pytest.raises(FrozenInstanceError):
        quality.window_chars = 1
    descriptions = list_generation_profiles()
    descriptions[0]['window_chars'] = 1
    assert list_generation_profiles()[0]['window_chars'] == 14_000
    monkeypatch.setenv('PROJECTHUB_GENERATION_PROFILE', 'economy')
    assert get_generation_profile().source_budget_chars == 60_000
    assert get_generation_profile('balanced').window_chars == 18_000
    assert get_generation_profile('economy').default_effort == 'low'
    assert all(get_generation_profile(name).experimental for name in ('balanced', 'economy'))


@pytest.mark.parametrize('profile', ['quality', 'balanced', 'economy'])
def test_profile_file_and_environment_precedence(tmp_path, profile):
    path = tmp_path / 'settings.toml'
    path.write_text(f'[ai]\ngeneration_profile = "{profile}"\n')
    env = {'PROJECTHUB_CONFIG': str(path)}
    assert config.load_settings(env).generation_profile == profile
    override = 'economy' if profile != 'economy' else 'quality'
    assert config.load_settings({**env, 'PROJECTHUB_GENERATION_PROFILE': override}).generation_profile == override


@pytest.mark.parametrize('value', ['"unknown"', 'true', '3', '"do-not-expose-this"'])
def test_invalid_profile_file_has_sanitized_error(tmp_path, value):
    path = tmp_path / 'settings.toml'
    path.write_text(f'[ai]\ngeneration_profile = {value}\n')
    with pytest.raises(config.ConfigError) as error:
        config.load_settings({'PROJECTHUB_CONFIG': str(path)})
    assert str(error.value) == 'ai.generation_profile must be quality, balanced or economy.'


def test_invalid_direct_profile_does_not_partially_change_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '2')
    monkeypatch.setenv('PROJECTHUB_GENERATION_PROFILE', 'quality')
    with pytest.raises(config.ConfigError):
        config.Settings(8766, tmp_path, 600, None, extraction_workers=4, generation_profile=None).apply_environment()
    assert os.environ['PROJECTHUB_EXTRACTION_WORKERS'] == '2'
    assert os.environ['PROJECTHUB_GENERATION_PROFILE'] == 'quality'


def test_profile_read_and_override_do_not_touch_config(settings_client, monkeypatch):
    client, path = settings_client
    original = path.read_bytes()
    monkeypatch.setenv('PROJECTHUB_GENERATION_PROFILE', 'economy')
    result = client.get('/api/settings/ai').json()
    assert result['generation_profile'] == 'economy'
    assert result['configured_generation_profile'] == 'quality'
    assert [profile['id'] for profile in result['generation_profiles']] == ['quality', 'balanced', 'economy']
    assert path.read_bytes() == original


def test_profile_only_patch_preserves_workers_and_comments(settings_client, monkeypatch):
    client, path = settings_client
    before = tomllib.loads(path.read_text())
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '12')
    result = client.patch('/api/settings/ai', json={'generation_profile': 'economy'})
    assert result.status_code == 200, result.text
    after = tomllib.loads(path.read_text())
    before['ai']['generation_profile'] = 'economy'
    assert after == before
    assert '# Preserve this comment.' in path.read_text()
    assert 'extraction_workers = 2 # measured separately' in path.read_text()
    assert result.json()['extraction_workers'] == 12
    assert result.json()['configured_extraction_workers'] == 2
    assert os.environ['PROJECTHUB_GENERATION_PROFILE'] == 'economy'
    assert os.environ['PROJECTHUB_EXTRACTION_WORKERS'] == '12'


def test_both_fields_are_saved_in_one_atomic_replace(settings_client, monkeypatch):
    client, path = settings_client
    replace = config.os.replace
    replaced = []
    def observed(source, target):
        proposed = tomllib.loads(Path(source).read_text())
        replaced.append((proposed['ai']['extraction_workers'], proposed['ai']['generation_profile']))
        return replace(source, target)
    monkeypatch.setattr(config.os, 'replace', observed)
    response = client.patch('/api/settings/ai', json={'extraction_workers': 3, 'generation_profile': 'balanced'})
    assert response.status_code == 200, response.text
    assert replaced == [(3, 'balanced')]
    assert response.json()['generation_profile'] == response.json()['configured_generation_profile'] == 'balanced'


@pytest.mark.parametrize('body', [
    {'generation_profile': None}, {'generation_profile': True}, {'generation_profile': 2},
    {'generation_profile': 'unknown'}, {'extraction_workers': None},
    {'generation_profile': 'economy', 'extraction_workers': -1},
    {'generation_profile': 'economy', 'extraction_workers': None},
])
def test_invalid_profiles_and_mixed_requests_leave_everything_unchanged(settings_client, body):
    client, path = settings_client
    original = path.read_bytes()
    previous = {key: os.environ.get(key) for key in ('PROJECTHUB_EXTRACTION_WORKERS', 'PROJECTHUB_GENERATION_PROFILE')}
    assert client.patch('/api/settings/ai', json=body).status_code == 422
    assert path.read_bytes() == original
    assert previous == {key: os.environ.get(key) for key in previous}


def test_combined_save_failure_preserves_file_and_both_runtime_values(settings_client, monkeypatch):
    client, path = settings_client
    original = path.read_bytes()
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '2')
    monkeypatch.setenv('PROJECTHUB_GENERATION_PROFILE', 'quality')
    def denied(*args):
        raise PermissionError('private path do-not-expose-this')
    monkeypatch.setattr(config.os, 'replace', denied)
    response = client.patch('/api/settings/ai', json={'extraction_workers': 3, 'generation_profile': 'economy'})
    assert response.status_code == 503 and 'do-not-expose-this' not in response.text
    assert path.read_bytes() == original
    assert os.environ['PROJECTHUB_EXTRACTION_WORKERS'] == '2'
    assert os.environ['PROJECTHUB_GENERATION_PROFILE'] == 'quality'
    assert not list(path.parent.glob('.projecthub-settings-*'))


@pytest.mark.parametrize('content', [
    '["ai"]\n"generation_profile" = "quality" # preference\nextraction_workers = 2\n',
    "['ai']\n'generation_profile' = 'quality' # preference\nextraction_workers = 2\n",
    '[ai]\r\ngeneration_profile = "quality" # preference\r\nextraction_workers = 2\r\n',
])
def test_quoted_and_crlf_profile_layouts_preserve_unrelated_fields(settings_client, content):
    client, path = settings_client
    path.write_bytes(content.encode())
    response = client.patch('/api/settings/ai', json={'generation_profile': 'balanced'})
    assert response.status_code == 200, response.text
    expected = tomllib.loads(content)
    expected['ai']['generation_profile'] = 'balanced'
    assert tomllib.loads(path.read_bytes().decode()) == expected
    assert '# preference' in path.read_text()
    if '\r\n' in content:
        assert b'\r\n' in path.read_bytes()


def test_serialized_independent_saves_keep_both_changes(settings_client):
    _, path = settings_client
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(config.save_ai_settings, generation_profile='balanced')
        second = pool.submit(config.save_ai_worker_settings, 3)
        first.result(); second.result()
    data = tomllib.loads(path.read_text())
    assert data['ai']['generation_profile'] == 'balanced'
    assert data['ai']['extraction_workers'] == 3


def test_worker_only_save_does_not_rewrite_profile_override(settings_client, monkeypatch):
    client, path = settings_client
    monkeypatch.setenv('PROJECTHUB_GENERATION_PROFILE', 'economy')
    response = client.patch('/api/settings/ai', json={'extraction_workers': 3})
    assert response.status_code == 200
    assert response.json()['generation_profile'] == 'economy'
    assert response.json()['configured_generation_profile'] == 'quality'
    assert 'generation_profile' not in tomllib.loads(path.read_text())['ai']
