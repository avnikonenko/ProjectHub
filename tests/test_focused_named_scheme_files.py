"""Explicit file scopes use the existing repository safeguards and record skips."""
import json

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
