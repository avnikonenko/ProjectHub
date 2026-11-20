from importlib import import_module

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.main import create_app
from backend.app.models import CodexProfile
from backend.app.integrations.codex.protocol import CodexError

routes = import_module('backend.app.integrations.codex.router')


@pytest.mark.parametrize(('installed', 'authenticated', 'app_server', 'expected'), [
    (False, False, False, 'not_installed'),
    (True, False, True, 'unavailable'),
    (True, True, False, 'unavailable'),
    (True, True, True, 'connected'),
])
def test_codex_status_probes_launch_account_without_exposing_cli_output(
    monkeypatch, installed, authenticated, app_server, expected,
):
    def capability(directory=None):
        assert directory is None
        return {'installed': installed, 'authenticated': authenticated, 'app_server': app_server,
                'version': 'private email@example.test', 'error': 'private auth output'}

    monkeypatch.setattr(routes, 'availability', capability)
    result = routes.ai_profile_status(None, 'codex').model_dump(exclude_none=True)
    assert result['state'] == expected
    if expected == 'connected':
        assert result == {'state': 'connected'}
    else:
        assert 'email@example.test' not in result['detail']
        assert 'private' not in result['detail']


@pytest.mark.parametrize(('installed', 'signed_in', 'expected'), [
    (False, False, 'not_installed'),
    (True, False, 'unavailable'),
    (True, True, 'connected'),
])
def test_claude_status_probes_only_authentication_and_discards_identity(monkeypatch, installed, signed_in, expected):
    monkeypatch.setattr(routes.shutil, 'which', lambda binary: f'/bin/{binary}' if installed else None)
    calls = []

    def verify_account(manager):
        calls.append(manager._directory)
        if not signed_in:
            raise CodexError('A private identity email@example.test failed.')
        manager.generation_account = 'email@example.test'
        return manager

    monkeypatch.setattr(routes.ClaudeGenerationManager, 'verify_account', verify_account)
    result = routes.ai_profile_status(None, 'claude').model_dump(exclude_none=True)
    assert result['state'] == expected
    assert calls == ([None] if installed else [])
    if expected == 'connected':
        assert result == {'state': 'connected'}
    else:
        assert 'email@example.test' not in result['detail']


def test_diagnostics_resolve_profiles_by_provider_and_reject_unavailable_profiles(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path / 'data')
    codex_home = tmp_path / 'private-codex'
    claude_home = tmp_path / 'private-claude'
    codex_home.mkdir()
    claude_home.mkdir()
    checked = []

    def codex_status(directory):
        checked.append(('codex', directory))
        return {'installed': True, 'authenticated': True, 'app_server': True}

    def claude_status(manager):
        checked.append(('claude', manager._directory))

    monkeypatch.setattr(routes, 'availability', codex_status)
    monkeypatch.setattr(routes.shutil, 'which', lambda binary: f'/bin/{binary}')
    monkeypatch.setattr(routes.ClaudeGenerationManager, 'verify_account', claude_status)
    with Session(engine) as db:
        db.add_all([CodexProfile(id=1, name='Private Codex', directory=str(codex_home), provider='codex'),
                    CodexProfile(id=2, name='Private Claude', directory=str(claude_home), provider='claude'),
                    CodexProfile(id=3, name='Deleted profile', directory=str(tmp_path / 'missing'), provider='claude')])
        db.commit()
        assert routes.ai_profile_status(db, 'codex', 'registered:1').state == 'connected'
        assert routes.ai_profile_status(db, 'claude', 'registered:2').state == 'connected'
        invalid = [('claude', 'registered:1'), ('codex', 'registered:2'), ('claude', 'registered:3'),
                   ('codex', 'registered:99999999999999999999'), ('claude', '../../private'), ('codex', '')]
        for provider, profile in invalid:
            result = routes.ai_profile_status(db, provider, profile)
            assert result.state == 'unavailable'
            assert 'private' not in result.detail
    assert checked == [('codex', str(codex_home)), ('claude', str(claude_home))]
    engine.dispose()


def test_settings_exposes_both_provider_statuses_over_http(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    probed = []

    def codex_status():
        probed.append('codex')
        return {'installed': False}

    def claude_binary(binary):
        if binary == 'claude':
            probed.append('claude')
            return '/bin/claude'
        return None

    monkeypatch.setattr(routes, 'availability', codex_status)
    monkeypatch.setattr(routes.shutil, 'which', claude_binary)
    monkeypatch.setattr(routes.ClaudeGenerationManager, 'verify_account', lambda self: self)
    with TestClient(create_app(tmp_path / 'api')) as client:
        endpoint = '/api/settings/ai-profiles/status'
        for provider in ('codex', 'claude'):
            for site in ('cross-site', 'same-site'):
                blocked = client.get(endpoint, params={'provider': provider},
                                     headers={'Sec-Fetch-Site': site, 'Sec-Fetch-Dest': 'image'})
                assert blocked.status_code == 403
        assert probed == []

        codex = client.get(endpoint, params={'provider': 'codex'})
        claude = client.get(endpoint, params={'provider': 'claude'}, headers={'Sec-Fetch-Site': 'same-origin'})
        assert codex.status_code == claude.status_code == 200
        assert codex.json()['state'] == 'not_installed'
        assert claude.json() == {'state': 'connected'}
        assert probed == ['codex', 'claude']
        assert client.get(endpoint, params={'provider': 'other'}).status_code == 422
