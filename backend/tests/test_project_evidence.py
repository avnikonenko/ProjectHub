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
from backend.app.models import CodexSession, Note, Project, Task
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
    assert 'No independently recorded test execution' in data['validation']
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
