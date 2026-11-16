"""Deterministic Scheme inspection, caching, boundaries, and project isolation."""
import json
import subprocess

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import CodexSession, Note, Project
from backend.app.integrations.codex import scheme_evidence as evidence


def repository(tmp_path):
    path = tmp_path / 'repo'
    path.mkdir()
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    return path


def setup(tmp_path, path=None):
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add_all([Project(name='One', description='A local architecture tool', repository_path=str(path) if path else None),
                    Project(name='Two', description='FOREIGN PROJECT')])
        db.flush()
        db.add_all([CodexSession(id=1, project_id=1, thread_id='one', history_indexed_at='now'),
                    CodexSession(id=2, project_id=2, thread_id='two'),
                    Note(project_id=1, title='Design', content='Use a pipeline.'),
                    Note(project_id=2, title='Foreign', content='FOREIGN NOTE')])
        db.execute(text('INSERT INTO codex_history_turns '
            '(project_id,session_id,session_title,turn_id,status,messages,content) '
            'VALUES (1,1,\'One\',\'a\',\'completed\',\'[]\',\'Pipeline source\'),'
            '(2,2,\'Two\',\'b\',\'completed\',\'[]\',\'FOREIGN TURN\')'))
        db.commit()
    return engine


def test_collects_manifests_and_safe_structure_without_executing_code(tmp_path):
    repo = repository(tmp_path)
    (repo / 'pyproject.toml').write_text('[project]\ndependencies=["fastapi>=0.115", "Pillow>=10"]\n'
        '[project.optional-dependencies]\ntest=["pytest>=8"]\n')
    (repo / 'requirements.txt').write_text('-r requirements-dev.txt\nnumpy>=2; python_version>="3.11"\n')
    (repo / 'requirements-dev.txt').write_text('ruff>=0.5\n')
    (repo / 'package.json').write_text(json.dumps({'dependencies': {'react': '^19'}, 'devDependencies': {'vite': '^6'}}))
    (repo / 'main.py').write_text('import fastapi\nclass App: pass\nraise RuntimeError("MUST NOT EXECUTE")\n')
    (repo / '.env').write_text('PASSWORD=NEVER_INCLUDE')
    (repo / 'credentials.json').write_text('NEVER_INCLUDE')
    (repo / '.gitignore').write_text('ignored.py\n')
    (repo / 'ignored.py').write_text('NEVER_INCLUDE')
    (repo / 'link.py').symlink_to(repo / '.env')
    engine = setup(tmp_path, repo)
    result = evidence.collect_evidence(engine, 1)
    deps = {d['name']: d for d in result['dependencies']}
    assert set(deps) == {'fastapi', 'Pillow', 'pytest', 'numpy', 'ruff', 'react', 'vite'}
    assert deps['react']['constraint'] == '^19'
    assert deps['vite']['category'] == 'development'
    assert deps['ruff']['category'] == 'development'
    serialized = json.dumps(result)
    assert 'NEVER_INCLUDE' not in serialized
    assert all(name not in {x['path'] for x in result['tree']} for name in ('.env', 'credentials.json', 'ignored.py', 'link.py'))
    assert 'FOREIGN' not in serialized
    assert 'main.py' in {x['path'] for x in result['tree']}
    assert result['sources'] and all(len(s['content_hash']) == 64 for s in result['sources'])
    assert result['coverage']['conversation_excerpts'] == 1
    engine.dispose()


def test_unchanged_repository_reuses_cached_extraction_and_invalidates_changes(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('class One: pass\n')
    engine = setup(tmp_path, repo)
    first = evidence.collect_evidence(engine, 1)
    read = evidence._read
    monkeypatch.setattr(evidence, '_read', lambda *args: pytest.fail('Unchanged files were reread'))
    cached = evidence.collect_evidence(engine, 1)
    assert cached['diagnostics']['cache_hits'] == 1
    assert cached['fingerprint'] == first['fingerprint']
    monkeypatch.setattr(evidence, '_read', read)
    (repo / 'main.py').write_text('class Two: pass\n')
    changed = evidence.collect_evidence(engine, 1)
    assert changed['fingerprint'] != first['fingerprint']
    with Session(engine) as db:
        db.execute(text("UPDATE codex_history_turns SET content='Changed discussion' WHERE project_id=1"))
        db.commit()
    assert evidence.collect_evidence(engine, 1)['fingerprint'] != changed['fingerprint']
    engine.dispose()


def test_requirements_include_is_bounded_and_urls_are_not_exposed(tmp_path):
    repo = repository(tmp_path)
    (repo / 'requirements.txt').write_text('-r ../outside.txt\n-r common.txt\n'
        'lib @ https://user:password@example.test/pkg.whl\n')
    (repo / 'common.txt').write_text('-r requirements.txt\nscipy>=1.0\n')
    (tmp_path / 'outside.txt').write_text('FOREIGN_PACKAGE')
    engine = setup(tmp_path, repo)
    result = evidence.collect_evidence(engine, 1)
    assert {d['name'] for d in result['dependencies']} == {'lib', 'scipy'}
    assert 'password' not in json.dumps(result)
    assert 'FOREIGN_PACKAGE' not in json.dumps(result)
    assert any('include' in msg for msg in result['coverage']['limitations'])
    engine.dispose()


def test_missing_repository_and_unknown_project(tmp_path):
    engine = setup(tmp_path)
    result = evidence.collect_evidence(engine, 1)
    assert result['coverage']['partial']
    assert result['tree'] == []
    assert result['sources'][0]['provenance'] == 'project metadata'
    with pytest.raises(HTTPException) as exc:
        evidence.collect_evidence(engine, 999)
    assert exc.value.status_code == 404
    with Session(engine) as db:
        p = db.get(Project, 1)
        p.repository_path = str(tmp_path / 'missing')
        db.commit()
    assert evidence.collect_evidence(engine, 1)['coverage']['partial']
    engine.dispose()


def test_user_exclusions_and_oversized_source_are_reported(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('x' * 70000)
    (repo / 'extra.py').write_text('class Hidden: pass')
    monkeypatch.setenv('PROJECTHUB_EVIDENCE_EXCLUDE', '["extra.py"]')
    engine = setup(tmp_path, repo)
    result = evidence.collect_evidence(engine, 1)
    assert 'extra.py' not in {x['path'] for x in result['tree']}
    assert not any(s.get('path') == 'main.py' for s in result['sources'])
    assert any('64 KiB' in msg for msg in result['coverage']['limitations'])
    engine.dispose()


def test_production_entrypoints_and_imported_modules_survive_utility_noise(tmp_path):
    repo = repository(tmp_path)
    files = {
        'pyproject.toml': '[project]\ndependencies=["fastapi>=0.115"]\n',
        'frontend/package.json': '{"dependencies":{"react":"^19"}}',
        'backend/app/main.py': 'from .router import routes\nclass Application: pass\n',
        'backend/app/router.py': 'from .core.pipeline import prepare\ndef process():\n    return prepare()\n',
        'backend/app/core/pipeline.py': 'def prepare():\n    return None\n',
        'frontend/src/main.tsx': 'import App from "./App";\nexport function boot() { return App(); }\n',
        'frontend/src/App.tsx': 'import FlowView from "./features/FlowView";\nexport function App() { return FlowView(); }\n',
        'frontend/src/features/FlowView.tsx': 'export function FlowView() { return null; }\n',
    }
    for index in range(30):
        files[f'migrations/{index:04d}_old.py'] = 'def upgrade(): pass\n'
        files[f'scripts/tool_{index:02d}.py'] = 'def run(): pass\n'
        files[f'tests/test_{index:02d}.py'] = 'def test_case(): pass\n'
        files[f'backend/app/aaa_helper_{index:02d}.py'] = 'class Helper: pass\n'
    for path, content in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    engine = setup(tmp_path, repo)
    result = evidence.collect_evidence(engine, 1)
    selected = {source['path'] for source in result['sources'] if source.get('path')}
    assert {
        'backend/app/main.py', 'backend/app/router.py', 'backend/app/core/pipeline.py',
        'frontend/src/main.tsx', 'frontend/src/App.tsx', 'frontend/src/features/FlowView.tsx',
    } <= selected
    assert not any(path.startswith(('migrations/', 'scripts/', 'tests/')) for path in selected)
    assert result['coverage']['files_inspected'] > 24
    assert result['diagnostics']['bytes_read'] <= evidence._MAX_INSPECTION_BYTES
    assert len(result['sources']) < len(files)
    engine.dispose()


def test_structural_excerpts_keep_late_declarations_and_safe_call_relationships():
    imports = ''.join(f'import package_{index}\n' for index in range(100))
    source = imports + '''
def prepare(default="EXAMPLE_PRIVATE_DEFAULT"):
    result = manager.generate_brief("EXAMPLE_PRIVATE_PAYLOAD")
    validated = validate_output(result, "EXAMPLE_PRIVATE_ARGUMENT")
    save_revision(validated)
    return validated
'''
    excerpt = evidence._structural_excerpt(source, 'pipeline.py')
    assert 'def prepare' in excerpt
    assert all(name in excerpt for name in ('manager.generate_brief', 'validate_output', 'save_revision'))
    assert 'EXAMPLE_PRIVATE' not in excerpt
    assert len(excerpt) <= evidence._MAX_EXCERPT

    js = ''.join(f'import item{index} from "package_{index}";\n' for index in range(100)) + '''
export async function draw(secret = "EXAMPLE_PRIVATE_DEFAULT") {
    const text = 'imaginary.execute()';
    // phantom.run() is explanatory text, not a call.
    const diagrammer = await import('mermaid');
    return mermaid.render("EXAMPLE_PRIVATE_DIAGRAM_ID", secret);
}
'''
    excerpt = evidence._structural_excerpt(js, 'FlowView.tsx')
    assert 'draw' in excerpt
    assert 'mermaid' in excerpt and 'mermaid.render' in excerpt
    assert 'imaginary.execute' not in excerpt and 'phantom.run' not in excerpt
    assert 'EXAMPLE_PRIVATE' not in excerpt
    assert len(excerpt) <= evidence._MAX_EXCERPT


def test_large_frontend_entrypoint_uses_bounded_prefix_for_imported_components(tmp_path):
    repo = repository(tmp_path)
    app = repo / 'frontend/src/App.tsx'
    app.parent.mkdir(parents=True)
    app.write_text('import FlowView from "./features/FlowView";\n'
                   'export function App() { return FlowView(); }\n' + '// padding\n' * 7000)
    component = repo / 'frontend/src/features/FlowView.tsx'
    component.parent.mkdir()
    component.write_text('export function FlowView() { return null; }\n')
    engine = setup(tmp_path, repo)
    result = evidence.collect_evidence(engine, 1)
    selected = {source['path'] for source in result['sources'] if source.get('path')}
    assert {'frontend/src/App.tsx', 'frontend/src/features/FlowView.tsx'} <= selected
    assert result['coverage']['partial']
    assert any('prefix' in message.lower() or 'truncat' in message.lower()
               for message in result['coverage']['limitations'])
    engine.dispose()
