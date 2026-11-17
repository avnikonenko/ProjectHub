import json
import runpy
from pathlib import Path

import pytest


setuptools = pytest.importorskip('setuptools')


@pytest.fixture
def release_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(setuptools, 'setup', lambda **_: None)
    release_files = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'setup.py'))['release_files']
    monkeypatch.setitem(release_files.__globals__, 'ROOT', tmp_path)
    frontend = tmp_path / 'frontend' / 'dist'
    manifest_path = frontend / '.vite' / 'manifest.json'
    manifest = {
        'index.html': {'file': 'assets/main.js', 'isEntry': True, 'dynamicImports': ['scheme.js']},
        'scheme.js': {'file': 'assets/scheme.js', 'dynamicImports': ['flowDiagram.js']},
        'flowDiagram.js': {'file': 'assets/flowDiagram.js', 'css': ['assets/flowDiagram.css']},
    }
    for path in (
        frontend / 'index.html', frontend / 'assets' / 'main.js', frontend / 'assets' / 'scheme.js',
        frontend / 'assets' / 'flowDiagram.js', frontend / 'assets' / 'flowDiagram.css', manifest_path,
        tmp_path / 'migrations' / 'env.py', tmp_path / 'migrations' / 'versions' / '0001_initial.py',
        tmp_path / 'AI_RECOMMENDATIONS.md', tmp_path / 'skills' / 'projecthub-notes' / 'SKILL.md',
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    (frontend / 'index.html').write_text('<script type="module" src="/assets/main.js"></script>', encoding='utf-8')
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    return release_files, frontend, manifest_path, manifest


def test_release_includes_lazy_diagram_assets(release_inputs):
    release_files, _, manifest_path, _ = release_inputs
    sources = {source for source, _ in release_files()}
    assert manifest_path not in sources
    assert {source.name for source in sources} >= {'main.js', 'scheme.js', 'flowDiagram.js', 'flowDiagram.css'}


@pytest.mark.parametrize('file', ['flowDiagram.js', 'flowDiagram.css'])
def test_release_rejects_missing_lazy_diagram_assets(release_inputs, file):
    release_files, frontend, _, _ = release_inputs
    (frontend / 'assets' / file).unlink()
    with pytest.raises(RuntimeError, match=r'Vite manifest.*assets/flowDiagram'):
        release_files()


def test_release_rejects_detached_chunk_entry(release_inputs):
    release_files, _, manifest_path, manifest = release_inputs
    del manifest['flowDiagram.js']
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(RuntimeError, match='missing chunk entries'):
        release_files()


def test_release_rejects_manifest_from_another_build(release_inputs):
    release_files, frontend, manifest_path, manifest = release_inputs
    manifest['index.html']['file'] = 'assets/stale.js'
    (frontend / 'assets' / 'stale.js').touch()
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(RuntimeError, match='does not match index.html'):
        release_files()
