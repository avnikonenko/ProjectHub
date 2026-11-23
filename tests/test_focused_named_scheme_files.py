"""Explicit file scopes use the existing repository safeguards and record skips."""
import json
import subprocess

from backend.app.integrations.codex import scheme_evidence
from backend.tests.integrations.test_scheme_evidence import repository, setup


def test_focus_reads_named_build_schema_and_workflow_files(tmp_path):
    repo = repository(tmp_path)
    fixtures = {
        'Dockerfile': 'FROM python:3.11\nRUN pip install SECRET_BUILD_ARG\n',
        'Makefile': 'test: prepare\n\tpytest --token=SECRET_RECIPE_ARG\n',
        'db schema.sql': "CREATE TABLE audit_event (id INTEGER);\nINSERT INTO audit_event VALUES ('SECRET_SEED');",
        '.github/workflows/ci.yml': 'jobs:\n  validate:\n    steps:\n      - run: echo SECRET_WORKFLOW_ARG\n',
    }
    for relative, value in fixtures.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    engine = setup(tmp_path, repo)
    try:
        broad = scheme_evidence.collect_evidence(engine, 1)
        assert not set(fixtures) & {source.get('path') for source in broad['sources']}
        result = scheme_evidence.collect_evidence(engine, 1, scope=(
            'Trace deployment through Dockerfile, Makefile, `db schema.sql`, and .github/workflows/ci.yml.'))
        excerpts = {source.get('path'): source['text'] for source in result['sources']}
        assert set(fixtures) <= excerpts.keys()
        assert 'FROM' in excerpts['Dockerfile']
        assert 'test' in excerpts['Makefile']
        assert 'audit_event' in excerpts['db schema.sql']
        assert 'validate' in excerpts['.github/workflows/ci.yml']
        assert 'SECRET_' not in json.dumps(result)
        assert not result['coverage'].get('requested_file_omissions')
    finally:
        engine.dispose()


def test_focus_records_specific_reasons_for_named_files_without_evidence(tmp_path):
    repo = repository(tmp_path)
    (repo / 'reference.pdf').write_bytes(b'%PDF')
    (repo / 'hidden').mkdir()
    (repo / 'hidden' / 'service-token.yml').write_text('private: SECRET_FILE_BODY\n')
    (repo / 'large.sql').write_text(' ' * (scheme_evidence._MAX_BYTES + 1))
    engine = setup(tmp_path, repo)
    try:
        result = scheme_evidence.collect_evidence(engine, 1, scope=(
            'Inspect reference.pdf, service-token.yml, large.sql and absent.sql, then trace storage.run.'))
        omissions = {item['path']: item['reason'] for item in result['coverage']['requested_file_omissions']}
        assert omissions == {
            'reference.pdf': 'unsupported_requested_type',
            'service-token.yml': 'excluded_by_policy',
            'large.sql': 'requested_file_too_large',
            'absent.sql': 'outside_repository_inventory',
        }
        assert 'hidden/service-token.yml' not in json.dumps(result)
        assert 'SECRET_FILE_BODY' not in json.dumps(result)
    finally:
        engine.dispose()


def test_named_file_coverage_reports_unlinked_repository(tmp_path):
    engine = setup(tmp_path)
    try:
        result = scheme_evidence.collect_evidence(engine, 1, scope='Explain Dockerfile and schema.sql')
        assert result['coverage']['requested_file_omissions'] == [
            {'path': 'Dockerfile', 'reason': 'repository_unavailable'},
            {'path': 'schema.sql', 'reason': 'repository_unavailable'},
        ]
    finally:
        engine.dispose()


def test_focus_uses_tracked_template_by_basename_and_honors_explicit_path(tmp_path):
    repo = repository(tmp_path)
    for directory, body in {'templates': '&active\n secret="PRIVATE_TEMPLATE_VALUE"\n/\n#&optional\n#/\n',
                            'tests': '&fixture\n/\n', 'old_copy': '&scratch\n/\n'}.items():
        (repo / directory).mkdir()
        (repo / directory / 'methods.in').write_text(body)
    subprocess.run(['git', '-C', str(repo), 'add', 'templates/methods.in', 'tests/methods.in'], check=True)
    engine = setup(tmp_path, repo)
    try:
        first = scheme_evidence.collect_evidence(engine, 1, scope='Explain defaults in methods.in')
        templates = {source['path']: source['text'] for source in first['sources']
                     if source.get('path', '').endswith('methods.in')}
        assert templates == {'templates/methods.in':
                             'L1: Active namelist section &active\nL4: Commented-out namelist section &optional'}
        assert not first['coverage'].get('requested_file_omissions')
        second = scheme_evidence.collect_evidence(engine, 1, scope='Read old_copy/methods.in and methods.in')
        assert any(source.get('path') == 'old_copy/methods.in' for source in second['sources'])
    finally:
        engine.dispose()


def test_focus_keeps_named_entrypoint_handoff_after_old_prefix_limit(tmp_path):
    repo = repository(tmp_path)
    (repo / 'drive.py').write_text(
        'import argparse\nfrom operations import restore_checkpoint, run_backend\n'
        + '# padding\n' * 7000
        + 'def main():\n'
          '    parser = argparse.ArgumentParser()\n'
          '    parser.add_argument("--program", choices=["fast", "accurate"], default="fast")\n'
          '    args = parser.parse_args()\n'
          '    restore_checkpoint()\n'
          '    if args.program in ("fast", "accurate"):\n'
          '        run_backend(args.program)\n'
          'if __name__ == "__main__":\n'
          '    main()\n'
    )
    engine = setup(tmp_path, repo)
    try:
        result = scheme_evidence.collect_evidence(engine, 1, scope='Trace checkpoint and backend dispatch in drive.py')
        excerpt = next(source['text'] for source in result['sources'] if source.get('path') == 'drive.py')
        assert 'CLI --program choices: fast, accurate (default: fast)' in excerpt
        assert 'main: restore_checkpoint()' in excerpt
        assert 'run_backend(args.program)' in excerpt
        assert result['diagnostics']['bytes_read'] <= scheme_evidence.DEFAULT_SCHEME_READ_BYTES
    finally:
        engine.dispose()
