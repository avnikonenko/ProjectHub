"""Independent checks for evidence metadata compatibility and trust boundaries."""
import json

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.integrations.codex.brief import read_brief
from backend.app.models import GenerationRevision


def test_evidence_migration_preserves_legacy_brief_and_revision(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'data.sqlite3'))
    cfg = Config()
    cfg.set_main_option('script_location', 'migrations')
    snapshot = {'generated_at': 'before', 'sections': {'overview': []}, 'sources': [],
                'coverage': ['Legacy coverage'], 'project_map': None, 'generation': None}
    with engine.begin() as connection:
        cfg.attributes['connection'] = connection
        command.upgrade(cfg, '0012')
        connection.exec_driver_sql(
            "INSERT INTO projects(id,name,description,goals,status,git_remote,tags,architecture_mermaid,created_at,updated_at) "
            "VALUES(1,'Existing','','','planning','','[]','','now','now')")
        connection.exec_driver_sql(
            'INSERT INTO project_briefs(project_id,generated_at,sections,sources,coverage,project_map,generation) '
            'VALUES(1,?,?,?,?,NULL,NULL)',
            ('before', json.dumps(snapshot['sections']), '[]', json.dumps(snapshot['coverage'])))
        connection.exec_driver_sql(
            "INSERT INTO generation_revisions(project_id,kind,generated_at,snapshot) VALUES(1,'brief','before',?)",
            (json.dumps(snapshot),))
        command.upgrade(cfg, 'head')
    with Session(engine) as db:
        assert read_brief(db, 1) == snapshot
        assert db.scalar(select(GenerationRevision.snapshot)) == snapshot
    engine.dispose()


def test_evidence_status_is_local_scoped_and_does_not_expose_passages(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import sys
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    from backend.app.models import Project, ProjectBrief
    from backend.app.integrations.codex import router

    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    app = create_app(tmp_path / 'data')
    with Session(app.state.engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')]); db.flush()
        db.add(ProjectBrief(project_id=1, generated_at='last-success', sections={}, sources=[], coverage=[],
                            project_state={'fingerprint': 'baseline'}))
        db.commit()
    calls = []
    snapshot = {
        'objective': 'Example', 'git': None, 'git_error': None, 'git_checked_at': None,
        'history_indexed_at': 'indexed', 'validation': 'No test execution recorded.',
        'fingerprint': 'baseline',
        'sources': [{'id': 'note:1', 'text': 'A private project passage'}],
    }

    def collect(engine, project_id):
        calls.append(project_id)
        return dict(snapshot)

    def no_manager(*args, **kwargs):
        raise AssertionError('A local evidence check must not initialize an AI manager')

    # The handler imports this function locally. Replacing the module also keeps
    # this trust-boundary test independent of collector implementation details.
    monkeypatch.setitem(sys.modules, 'backend.app.project_evidence', SimpleNamespace(collect_project_evidence=collect))
    monkeypatch.setattr(router, 'manager_for', no_manager)
    with TestClient(app) as client:
        response = client.get('/api/projects/1/codex/evidence')
        assert response.status_code == 200
        assert response.json()['stale'] is False
        assert response.json()['analysis_generated_at'] == 'last-success'
        assert 'sources' not in response.json()
        assert 'private project passage' not in response.text
        snapshot['fingerprint'] = 'changed'
        assert client.get('/api/projects/1/codex/evidence').json()['stale'] is True
        snapshot['git_error'] = 'Repository unavailable'
        assert client.get('/api/projects/1/codex/evidence').json()['stale'] is None
        snapshot['git_error'] = None
        snapshot['git'] = {'change_detection_limited': True}
        assert client.get('/api/projects/1/codex/evidence').json()['stale'] is None
        assert client.get('/api/projects/2/codex/evidence').json()['analysis_generated_at'] is None
        before = len(calls)
        assert client.get('/api/projects/99999/codex/evidence').status_code == 404
        assert len(calls) == before


def test_git_inspection_does_not_execute_repository_filter(tmp_path):
    import subprocess
    from backend.app.project_evidence import _git_evidence

    repo = tmp_path / 'repo'; repo.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True,
                              capture_output=True)
    git('init', '-q')
    git('config', 'user.email', 'test@example.invalid')
    git('config', 'user.name', 'Evidence test')
    (repo / 'sample.txt').write_text('before\n')
    git('add', 'sample.txt'); git('commit', '-qm', 'Initial')
    marker = tmp_path / 'FILTER_EXECUTED'
    # The repository controls this shell command; inspection must disable it.
    git('config', 'filter.malicious.clean', 'touch ' + str(marker))
    git('config', 'filter.malicious.required', 'true')
    (repo / '.gitattributes').write_text('sample.txt filter=malicious\n')
    (repo / 'sample.txt').write_text('after\n')
    result, _ = _git_evidence(repo, ())
    assert 'sample.txt' in result['modified']
    assert not marker.exists()


def test_file_markers_never_follow_file_or_parent_symlinks(tmp_path):
    import os
    from backend.app.project_evidence import _file_marker

    repo = tmp_path / 'repo'; repo.mkdir()
    private = tmp_path / 'private'; private.mkdir()
    (private / 'secret.txt').write_text('Private material')
    (repo / 'linked.txt').symlink_to(private / 'secret.txt')
    (repo / 'linked-dir').symlink_to(private, target_is_directory=True)
    descriptor = os.open(repo, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for relative in ('linked.txt', 'linked-dir/secret.txt', '../private/secret.txt'):
            marker, count = _file_marker(descriptor, relative, 1000)
            assert marker == {'unreadable': True}
            assert count == 0
    finally:
        os.close(descriptor)
