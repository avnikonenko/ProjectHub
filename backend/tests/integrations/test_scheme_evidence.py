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
