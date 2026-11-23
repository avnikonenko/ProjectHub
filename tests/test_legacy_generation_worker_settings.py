"""Both legacy entrypoints capture the shared setting for the complete job."""
from types import SimpleNamespace

import pytest

from backend.tests.integrations.test_archive import env
from backend.app.integrations.codex import archive, brief, qa
from backend.app.integrations.codex.protocol import CodexError


@pytest.mark.parametrize('module,entrypoint', [(brief, 'refresh_brief'), (qa, 'refresh_qa')])
def test_legacy_entrypoints_apply_shared_worker_changes_to_next_job(env, monkeypatch, module, entrypoint):
    engine, _ = env
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '5')
    manager = SimpleNamespace(independent_generation=lambda: None)
    observed = []

    def inspect_scope(*args):
        captured = archive._worker_count(manager)
        observed.append(captured)
        monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '2')
        assert archive._worker_count(manager) == captured
        raise CodexError('Stop after checking worker scope; no generation requested.')

    monkeypatch.setattr(module, 'archive_history', inspect_scope)
    for _ in range(2):
        with pytest.raises(CodexError, match='checking worker scope'):
            getattr(module, entrypoint)(engine, 1, manager)
        assert archive.archive_diagnostics() == {}
    assert observed == [5, 2]
