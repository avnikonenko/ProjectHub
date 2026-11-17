"""Implicit worker defaults agree across runtime entry points and setup examples."""
from pathlib import Path
from types import SimpleNamespace
import tomllib

from backend.app.config import DEFAULT_EXTRACTION_WORKERS, TEMPLATE, load_settings
from backend.app.integrations.codex import archive, knowledge


def test_unconfigured_runtime_and_setup_defaults_agree(tmp_path, monkeypatch):
    monkeypatch.delenv('PROJECTHUB_CONFIG', raising=False)
    monkeypatch.delenv('PROJECTHUB_EXTRACTION_WORKERS', raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    example = Path(__file__).resolve().parents[1] / 'config.example.toml'
    assert DEFAULT_EXTRACTION_WORKERS == 10
    assert tomllib.loads(example.read_text())['ai']['extraction_workers'] == DEFAULT_EXTRACTION_WORKERS
    assert tomllib.loads(TEMPLATE)['ai']['extraction_workers'] == DEFAULT_EXTRACTION_WORKERS
    assert load_settings().extraction_workers == DEFAULT_EXTRACTION_WORKERS
    manager = SimpleNamespace(independent_generation=lambda: None)
    assert knowledge._extraction_workers(manager) == DEFAULT_EXTRACTION_WORKERS
    with archive.archive_worker_scope():
        assert archive._worker_count(manager) == DEFAULT_EXTRACTION_WORKERS
    assert knowledge._extraction_workers(object()) == 1
