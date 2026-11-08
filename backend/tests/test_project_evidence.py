"""Real SQLite and disposable Git evidence, without model calls or project execution."""
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi import HTTPException
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import CodexSession, Decision, Note, Project, Task
from backend.app import project_evidence as evidence


@pytest.fixture
def env(tmp_path):
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add_all([Project(id=1, name='Garden', goals='Prepare garden beds'),
                    Project(id=2, name='Private project')])
        db.flush()
        db.add_all([CodexSession(id=1, project_id=1, thread_id='one', title='Garden planning'),
                    CodexSession(id=2, project_id=2, thread_id='two', title='Private session'),
                    CodexSession(id=3, project_id=1, thread_id='three', title='Unlinked', is_linked=False)])
        db.add_all([Note(project_id=1, title='Plan', content='Use raised beds.'),
                    Note(project_id=2, title='Private note', content='Foreign evidence.'),
                    Task(project_id=1, title='Prepare beds', status='in_progress', description='Await materials.'),
                    Task(project_id=2, title='Private task')])
        db.commit()
    yield engine
    engine.dispose()


def add_turn(engine, project_id, session_id, content, stamp='first'):
    with engine.begin() as db:
        db.execute(text('INSERT INTO codex_history_turns '
                        '(project_id,session_id,session_title,turn_id,status,messages,content) '
                        'VALUES (:pid,:sid,:title,:tid,:status,:messages,:content)'), {
            'pid': project_id, 'sid': session_id, 'title': 'Conversation',
            'tid': f'turn-{session_id}', 'status': 'completed', 'messages': '[]', 'content': content,
        })
        db.execute(text('INSERT INTO codex_history_index(project_id,indexed_at,indexed_turns) '
                        'VALUES(:pid,:stamp,1) ON CONFLICT(project_id) DO UPDATE SET indexed_at=:stamp'),
                   {'pid': project_id, 'stamp': stamp})


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def repository(tmp_path, *, commit=True):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-b', 'work')
    git(repo, 'config', 'user.name', 'Example')
    git(repo, 'config', 'user.email', 'example@example.test')
    (repo / 'README.md').write_text('Example project\n')
    (repo / 'pyproject.toml').write_text('[project]\nname="example"\n')
    (repo / 'test_example.py').write_text('def test_example(): pass\n')
    (repo / 'source.py').write_text('print("original")\n')
    if commit:
        git(repo, 'add', '.')
        git(repo, 'commit', '-m', 'Initial example')
    return repo


def link(engine, repo):
    with Session(engine) as db:
        db.get(Project, 1).repository_path = str(repo)
        db.commit()


def test_reported_sources_are_scoped_bounded_and_not_validation(env):
    add_turn(env, 1, 1, 'Garden discussion: ' + 'a' * 5000 + ' end of discussion')
    add_turn(env, 2, 2, 'Foreign evidence')
    add_turn(env, 1, 3, 'Unlinked evidence', stamp='new')
    data = evidence.collect_project_evidence(env, 1)
    assert data['objective'] == 'Prepare garden beds'
    assert data['git'] is None and data['git_error'] is None
    assert data['history_indexed_at'] == 'new'
    assert data['validation'] == 'No verified test or validation results.'
    assert {source['classification'] for source in data['sources']} == {'reported'}
    serialized = json.dumps(data)
    assert 'Private' not in serialized and 'Foreign evidence' not in serialized and 'Unlinked evidence' not in serialized
    assert any(source['id'] == 'task:1' and 'in_progress' in source['text'] for source in data['sources'])
    assert all(len(source['text']) <= 1800 for source in data['sources'])
    with pytest.raises(HTTPException) as exc:
        evidence.collect_project_evidence(env, 999)
    assert exc.value.status_code == 404


def test_fingerprint_detects_records_and_content_but_not_identical_reindex(env):
    add_turn(env, 1, 1, 'Garden planning')
    first = evidence.collect_project_evidence(env, 1)
    assert evidence.collect_project_evidence(env, 1)['fingerprint'] == first['fingerprint']
    with env.begin() as db:
        db.execute(text("UPDATE codex_history_index SET indexed_at='second' WHERE project_id=1"))
    second = evidence.collect_project_evidence(env, 1)
    assert second['fingerprint'] == first['fingerprint']
    with env.begin() as db:
        db.execute(text("UPDATE codex_history_turns SET content='Changed garden planning' WHERE session_id=1"))
        db.execute(text("UPDATE codex_history_index SET indexed_at='third' WHERE project_id=1"))
    third = evidence.collect_project_evidence(env, 1)
    assert third['fingerprint'] != second['fingerprint']
    with Session(env) as db:
        db.add_all([Note(project_id=1, title=f'Extra {i}', content='content') for i in range(12)])
        db.commit()
    before = evidence.collect_project_evidence(env, 1)
    assert not any(source['id'] == 'note:1' for source in before['sources'])
    with Session(env) as db:
        db.get(Note, 1).content = 'Changed old note outside the source excerpt limit'
        db.commit()
    assert evidence.collect_project_evidence(env, 1)['fingerprint'] != before['fingerprint']


def test_git_head_paths_same_path_changes_and_read_only_collection(env, tmp_path, monkeypatch):
    repo = repository(tmp_path)
    link(env, repo)
    first = evidence.collect_project_evidence(env, 1)
    state = first['git']
    assert first['git_error'] is None
    assert state['branch'] == 'work' and state['head'] == git(repo, 'rev-parse', 'HEAD')
    assert not state['dirty']
    assert 'README.md' in state['docs'] and 'pyproject.toml' in state['dependency_manifests']
    assert 'test_example.py' in state['test_configs']
    assert all(source['classification'] == 'verified' for source in first['sources'] if source['id'].startswith(('git:', 'repository:')))
    (repo / 'source.py').write_text('print("modified")\n')
    (repo / 'new.txt').write_text('new file')
    (repo / '.env').write_text('SECRET=do-not-read')
    (repo / '.gitattributes').write_text('source.py filter=custom\n')
    git(repo, 'config', 'filter.custom.clean', 'touch ran-filter; cat')
    git(repo, 'config', 'core.fsmonitor', 'touch ran-monitor')
    before_index = (repo / '.git/index').read_bytes()
    monkeypatch.setenv('GIT_DIR', str(tmp_path / 'wrong'))
    changed = evidence.collect_project_evidence(env, 1)
    assert changed['git']['dirty']
    assert 'source.py' in changed['git']['modified'] and 'new.txt' in changed['git']['untracked']
    assert '.env' not in json.dumps(changed)
    assert not (repo / 'ran-filter').exists() and not (repo / 'ran-monitor').exists()
    assert (repo / '.git/index').read_bytes() == before_index
    (repo / 'source.py').write_text('print("different")\n')
    assert evidence.collect_project_evidence(env, 1)['fingerprint'] != changed['fingerprint']


def test_exclusions_and_symlinks_do_not_read_private_content(env, tmp_path, monkeypatch):
    repo = repository(tmp_path)
    link(env, repo)
    private = tmp_path / 'private'
    private.mkdir()
    (private / 'credentials.txt').write_text('never read this')
    (repo / 'external').symlink_to(private, target_is_directory=True)
    (repo / 'linked-file').symlink_to(private / 'credentials.txt')
    (repo / 'local-data').mkdir()
    (repo / 'local-data/secret.txt').write_text('private')
    monkeypatch.setenv('PROJECTHUB_EVIDENCE_EXCLUDE', '["local-data","README.md"]')
    original = evidence._file_marker
    read_paths = []
    def marker(fd, path, budget):
        read_paths.append(path)
        return original(fd, path, budget)
    monkeypatch.setattr(evidence, '_file_marker', marker)
    data = evidence.collect_project_evidence(env, 1)
    assert data['git_error'] is None
    assert 'README.md' not in data['git']['structure']
    assert not any('local-data' in path for path in read_paths)
    descriptor = os.open(repo, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert original(descriptor, 'external/credentials.txt', 100)[0] == {'unreadable': True}
    finally:
        os.close(descriptor)
    assert data['git']['change_detection_limited'] is True


def test_change_detection_marks_skipped_content_as_incomplete(env, tmp_path, monkeypatch):
    repo = repository(tmp_path)
    link(env, repo)
    (repo / 'one.txt').write_text('one')
    (repo / 'two.txt').write_text('two')
    monkeypatch.setattr(evidence, '_MAX_CHANGED_FILES', 1)
    data = evidence.collect_project_evidence(env, 1)
    assert data['git_error'] is None
    assert data['git']['change_detection_limited'] is True


def test_git_staging_deletions_and_renames_have_complete_distinct_fingerprints(env, tmp_path):
    repo = repository(tmp_path)
    link(env, repo)
    (repo / 'source.py').write_text('modified\n')
    unstaged = evidence.collect_project_evidence(env, 1)
    git(repo, 'add', 'source.py')
    staged = evidence.collect_project_evidence(env, 1)
    assert staged['fingerprint'] != unstaged['fingerprint']
    assert not staged['git']['change_detection_limited']
    # Changing only the staged blob can leave both porcelain columns unchanged.
    (repo / 'source.py').write_text('working copy\n')
    partially_staged = evidence.collect_project_evidence(env, 1)
    blob = subprocess.run(['git', '-C', str(repo), 'hash-object', '-w', '--stdin'],
                          input='alternate index\n', check=True, capture_output=True, text=True).stdout.strip()
    git(repo, 'update-index', '--cacheinfo', f'100644,{blob},source.py')
    restaged = evidence.collect_project_evidence(env, 1)
    assert restaged['fingerprint'] != partially_staged['fingerprint']
    (repo / 'README.md').unlink()
    deleted = evidence.collect_project_evidence(env, 1)
    assert 'README.md' in deleted['git']['modified']
    assert not deleted['git']['change_detection_limited']
    assert evidence.collect_project_evidence(env, 1)['fingerprint'] == deleted['fingerprint']
    git(repo, 'add', 'source.py')
    git(repo, 'mv', 'source.py', 'renamed.py')
    renamed = evidence.collect_project_evidence(env, 1)
    assert {'source.py', 'renamed.py'} <= set(renamed['git']['modified'])
    assert not renamed['git']['change_detection_limited']
    assert renamed['fingerprint'] != deleted['fingerprint']


def test_fingerprint_tracks_only_project_accepted_decision_content(env):
    with Session(env) as db:
        db.add_all([
            Decision(id=1, project_id=1, title='Accepted', status='accepted', evidence='x' * 10000),
            Decision(id=2, project_id=2, title='Foreign accepted', status='accepted'),
            Decision(id=3, project_id=1, title='Proposal', status='proposed'),
        ])
        db.commit()
    first = evidence.collect_project_evidence(env, 1)['fingerprint']
    with Session(env) as db:
        db.get(Decision, 2).evidence = 'Foreign edit'
        db.get(Decision, 3).evidence = 'Proposal edit'
        db.commit()
    assert evidence.collect_project_evidence(env, 1)['fingerprint'] == first
    with Session(env) as db:
        db.get(Decision, 1).evidence += 'Changed beyond excerpt limit'
        db.commit()
    changed = evidence.collect_project_evidence(env, 1)['fingerprint']
    assert changed != first
    with Session(env) as db:
        db.get(Decision, 1).status = 'proposed'
        db.commit()
    assert evidence.collect_project_evidence(env, 1)['fingerprint'] != changed


def test_empty_detached_and_worktree_repositories(env, tmp_path):
    repo = repository(tmp_path, commit=False)
    link(env, repo)
    state = evidence.collect_project_evidence(env, 1)['git']
    assert state['branch'] == 'work' and state['head'] is None and state['commits'] == []
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'Initial')
    git(repo, 'checkout', '--detach', 'HEAD')
    assert evidence.collect_project_evidence(env, 1)['git']['branch'] is None
    worktree = tmp_path / 'worktree'
    git(repo, 'worktree', 'add', '-b', 'secondary', str(worktree))
    link(env, worktree)
    assert evidence.collect_project_evidence(env, 1)['git']['branch'] == 'secondary'


def test_repository_errors_are_explicit_and_sanitized(env, tmp_path, monkeypatch):
    link(env, tmp_path / 'missing')
    data = evidence.collect_project_evidence(env, 1)
    assert data['git'] is None and data['git_error']
    assert str(tmp_path) not in data['git_error']
    repo = repository(tmp_path)
    link(env, repo)
    monkeypatch.setenv('PROJECTHUB_EVIDENCE_EXCLUDE', '["do-not-print"')
    data = evidence.collect_project_evidence(env, 1)
    assert data['git'] is None and 'do-not-print' not in data['git_error']
    monkeypatch.delenv('PROJECTHUB_EVIDENCE_EXCLUDE')
    monkeypatch.setattr(evidence, '_GIT_TIMEOUT', 0)
    assert 'timed out' in evidence.collect_project_evidence(env, 1)['git_error']


def test_git_runner_limits_output_and_waits_for_no_project_commands(tmp_path, monkeypatch):
    popen = subprocess.Popen
    def noisy(*args, **kwargs):
        return popen([sys.executable, '-c', 'import sys;sys.stdout.write("x"*3000)'], **kwargs)
    monkeypatch.setattr(evidence.subprocess, 'Popen', noisy)
    with pytest.raises(evidence.EvidenceReadError, match='output limit'):
        evidence._git(tmp_path, 'status', limit=1024)

    def slow(*args, **kwargs):
        return popen([sys.executable, '-c', 'import time;time.sleep(3)'], **kwargs)
    monkeypatch.setattr(evidence.subprocess, 'Popen', slow)
    monkeypatch.setattr(evidence, '_GIT_TIMEOUT', 0.05)
    with pytest.raises(evidence.EvidenceReadError, match='timed out'):
        evidence._git(tmp_path, 'status')


def test_explicit_code_collection_reads_representative_tracked_files_with_bounds(tmp_path):
    repo = repository(tmp_path)
    for folder in ('backend', 'frontend', 'docs', 'tests'):
        (repo / folder).mkdir()
    files = {
        'backend/main.py': 'from fastapi import FastAPI\napp = FastAPI()\n' * 1000,
        'frontend/App.tsx': 'export function App() { return <main /> }\n' * 1000,
        'docs/architecture.md': 'React calls FastAPI, which stores data in SQLite.\n' * 100,
        'tests/test_api.py': 'def test_api(): pass\n' * 100,
        'backend/secrets.py': 'SECRET_DO_NOT_READ',
        'backend/settings.py': 'PASSWORD_DO_NOT_READ',
        'package-lock.json': '{"secret":"LOCK_DO_NOT_READ"}',
        'untracked.py': 'UNTRACKED_DO_NOT_READ',
    }
    for path, content in files.items():
        (repo / path).write_text(content)
    git(repo, 'add', 'backend', 'frontend', 'docs', 'tests', 'package-lock.json')
    git(repo, 'commit', '-m', 'Architecture evidence')
    before = (repo / '.git/index').read_bytes()
    result = evidence.collect_repository_code(str(repo))
    paths = {source['path'] for source in result['sources']}
    assert {'README.md', 'docs/architecture.md', 'backend/main.py', 'frontend/App.tsx', 'tests/test_api.py'} <= paths
    assert len(result['sources']) <= 12
    assert sum(len(source['text']) for source in result['sources']) <= 24000
    assert all(len(source['text']) <= 3000 for source in result['sources'])
    assert all(source['classification'] == 'verified' for source in result['sources'])
    serialized = json.dumps(result)
    assert not any(value in serialized for value in ('SECRET_DO_NOT_READ', 'PASSWORD_DO_NOT_READ',
                                                    'LOCK_DO_NOT_READ', 'UNTRACKED_DO_NOT_READ'))
    assert any('partial' in value for value in result['coverage'])
    assert (repo / '.git/index').read_bytes() == before


def test_explicit_code_collection_never_follows_links_and_respects_exclusions(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    private = tmp_path / 'private'
    private.mkdir()
    (private / 'outside.py').write_text('EXTERNAL_CODE_MUST_NOT_APPEAR')
    (repo / 'linked.py').symlink_to(private / 'outside.py')
    (repo / 'linked-dir').symlink_to(private, target_is_directory=True)
    (repo / 'binary.py').write_bytes(b'\x00BINARY_CODE_MUST_NOT_APPEAR')
    (repo / 'private.py').write_text('CUSTOM_EXCLUSION_MUST_NOT_APPEAR')
    git(repo, 'add', 'linked.py', 'linked-dir', 'binary.py', 'private.py')
    git(repo, 'commit', '-m', 'Unsafe candidates')
    monkeypatch.setenv('PROJECTHUB_EVIDENCE_EXCLUDE', '["private.py","README.md"]')
    result = evidence.collect_repository_code(str(repo))
    serialized = json.dumps(result)
    assert not any(value in serialized for value in ('EXTERNAL_CODE_MUST_NOT_APPEAR',
                                                    'BINARY_CODE_MUST_NOT_APPEAR',
                                                    'CUSTOM_EXCLUSION_MUST_NOT_APPEAR'))
    assert 'README.md' not in {source['path'] for source in result['sources']}
    assert any('Skipped' in value for value in result['coverage'])
    assert evidence.collect_repository_code(str(tmp_path / 'missing'))['sources'] == []
    assert evidence.collect_repository_code(None)['sources'] == []


def test_code_collection_includes_deep_route_declarations_instead_of_only_imports(tmp_path):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text(
        'from fastapi import FastAPI\n' + '# import and setup details\n' * 200
        + 'app = FastAPI()\n@app.get("/projects")\ndef list_projects():\n    return []\n')
    (repo / 'App.tsx').write_text(
        'import React from "react";\n' + '// types and state declarations\n' * 200
        + 'const tabs = [\n  "Dashboard",\n  "History",\n  "Notes",\n];\n')
    git(repo, 'add', 'main.py', 'App.tsx')
    git(repo, 'commit', '-m', 'Deep public declarations')
    result = evidence.collect_repository_code(str(repo))
    indexed = {source['path']: source['text'] for source in result['sources']}
    assert '@app.get("/projects")' in indexed['main.py']
    assert 'def list_projects' in indexed['main.py']
    assert '"Dashboard"' in indexed['App.tsx']
    assert len(indexed['main.py']) <= 2000
