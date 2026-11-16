"""Saved Scheme inventory counts from real local Git/files; no model calls."""
import json
import subprocess

import pytest

from backend.app.integrations.codex import scheme_evidence as evidence
from backend.tests.integrations.test_scheme_evidence import repository, setup


def inspect(tmp_path, repo):
    engine = setup(tmp_path, repo)
    try:
        return evidence.collect_evidence(engine, 1)
    finally:
        engine.dispose()


def counts(result):
    value = result['coverage']['file_counts']
    assert value['attempted_files'] == value['read_files'] + value['failed_reads']
    assert value['read_files'] == value['evidence_files'] + value['read_not_in_evidence']
    assert value['prefix_read_files'] <= value['read_files']
    assert value['evidence_files'] == len({s['path'] for s in result['sources'] if s.get('path')})
    if value['listing_complete']:
        assert value['repository_paths_total'] == value['safe_paths'] + value['excluded_paths']
        assert value['safe_paths'] == value['eligible_files'] + value['policy_omitted_paths']
        assert value['eligible_files'] == value['attempted_files'] + value['not_attempted_files']
    return value


def test_inventory_excludes_credentials_and_does_not_count_unlisted_ignored_files(tmp_path):
    repo = repository(tmp_path)
    for name, content in {'main.py': 'class Main: pass\n', '.env': 'SECRET',
                          '.gitignore': 'ignored.py\n', 'ignored.py': 'SECRET',
                          'tracked.py': 'class Ignored: pass\n', 'blob.xyz': 'data'}.items():
        (repo / name).write_text(content)
    subprocess.run(['git', '-C', str(repo), 'add', 'tracked.py'], check=True)
    with (repo / '.gitignore').open('a') as stream:
        stream.write('tracked.py\n')
    result = inspect(tmp_path, repo)
    value = counts(result)
    assert value['repository_paths_total'] == 5
    assert value['excluded_paths'] == 2
    assert value['safe_paths'] == 3
    assert value['eligible_files'] == value['read_files'] == value['evidence_files'] == 1
    assert value['policy_omitted_paths'] == 2
    assert 'SECRET' not in json.dumps(result)


def test_successful_reads_count_before_parser_failure_and_prefix_decode(tmp_path):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('class Main: pass\n' + '# padding\n' * 7000)
    (repo / 'binary.py').write_bytes(b'\0binary')
    (repo / 'pyproject.toml').write_text('[broken')
    (repo / 'empty.py').write_text('')
    result = inspect(tmp_path, repo)
    value = counts(result)
    assert value['attempted_files'] == 4
    assert value['read_files'] == 3
    assert value['prefix_read_files'] == 1
    assert value['failed_reads'] == 1
    assert value['evidence_files'] == 1
    assert value['read_not_in_evidence'] == 2


@pytest.mark.parametrize('limit', ['bytes', 'metadata', 'time'])
def test_budget_denominator_survives_partial_inspection(tmp_path, monkeypatch, limit):
    repo = repository(tmp_path)
    for i in range(3):
        (repo / f'module{i}.py').write_text('class Module: pass\n')
    if limit == 'bytes':
        monkeypatch.setattr(evidence, '_MAX_INSPECTION_BYTES', 1)
    elif limit == 'metadata':
        monkeypatch.setattr(evidence, '_MAX_METADATA_BYTES', 280)
    else:
        monkeypatch.setattr(evidence, '_INSPECTION_SECONDS', 0)
    result = inspect(tmp_path, repo)
    value = counts(result)
    assert value['listing_complete'] is True
    assert value['eligible_files'] == 3
    assert value['not_attempted_files'] >= 2
    if limit == 'metadata':
        assert value['read_files'] == 1
    else:
        assert value['evidence_files'] == 0
    assert result['coverage']['inspection_policy']['max_read_bytes'] == evidence._MAX_INSPECTION_BYTES


@pytest.mark.parametrize('kind', ['unlinked', 'missing', 'empty', 'no_code'])
def test_unavailable_totals_are_unknown_and_known_empty_totals_are_zero(tmp_path, kind):
    repo = None if kind == 'unlinked' else tmp_path / 'missing' if kind == 'missing' else repository(tmp_path)
    if kind == 'no_code':
        (repo / 'asset.xyz').write_text('asset')
    value = counts(inspect(tmp_path, repo))
    assert value['read_files'] == value['attempted_files'] == value['evidence_files'] == 0
    assert value['listing_complete'] is (kind in {'empty', 'no_code'})
    assert value['repository_paths_total'] == (0 if kind == 'empty' else 1 if kind == 'no_code' else None)
    assert value['eligible_files'] == (0 if kind in {'empty', 'no_code'} else None)


def test_final_package_drops_are_counted_and_cached_without_rereads(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    for i in range(10):
        (repo / f'feature{i}.py').write_text(f'class Feature{i}: pass\n')
    monkeypatch.setattr(evidence, '_MAX_PACKAGE_CHARS', 4000)
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        value = counts(result)
        assert value['read_files'] == 10
        assert value['evidence_files'] < 10
        payload = {key: result[key] for key in ('sources', 'dependencies', 'tree', 'coverage')}
        assert len(json.dumps(payload, ensure_ascii=False)) <= 4000
        assert result['diagnostics']['package_chars'] == len(json.dumps(payload, ensure_ascii=False))
        monkeypatch.setattr(evidence, '_read', lambda *a, **kw: pytest.fail('Cached evidence reread'))
        cached = evidence.collect_evidence(engine, 1)
        assert cached['diagnostics']['cache_hits'] == 1
        assert cached['coverage'] == result['coverage']
    finally:
        engine.dispose()


def test_excluded_path_invalidates_counts_cache_without_changing_source_fingerprint(tmp_path):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('class Main: pass\n')
    engine = setup(tmp_path, repo)
    try:
        first = evidence.collect_evidence(engine, 1)
        (repo / '.env').write_text('SECRET')
        updated = evidence.collect_evidence(engine, 1)
        assert updated['diagnostics']['cache_hits'] == 0
        assert updated['fingerprint'] == first['fingerprint']
        assert counts(updated)['repository_paths_total'] == counts(first)['repository_paths_total'] + 1
        assert counts(updated)['excluded_paths'] == counts(first)['excluded_paths'] + 1
    finally:
        engine.dispose()


def test_declared_package_entrypoint_expands_initial_candidate_set_safely(tmp_path):
    repo = repository(tmp_path)
    (repo / 'pyproject.toml').write_text('[project.scripts]\nlaunch="feature:main"\n')
    package = repo / 'feature'
    package.mkdir()
    (package / '__init__.py').write_text('def main(): pass\n')
    (repo / 'main.py').write_text('class Main: pass\n')
    value = counts(inspect(tmp_path, repo))
    assert value['eligible_files'] == value['read_files'] == value['evidence_files'] == 3
    assert value['policy_omitted_paths'] == 0


def test_source_text_budget_omissions_are_successful_reads(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('class Main: pass\n')
    monkeypatch.setattr(evidence, '_MAX_SOURCES_CHARS', 0)
    result = inspect(tmp_path, repo)
    value = counts(result)
    assert value['read_files'] == value['read_not_in_evidence'] == 1
    assert value['evidence_files'] == 0
    assert result['coverage']['omitted_reason_counts']['source_output_budget'] > 0


def test_repository_disappearing_after_listing_preserves_known_inventory_without_reads(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('class Main: pass\n')
    original_open = evidence.os.open

    def unavailable_root(path, *args, **kwargs):
        if path == repo:
            raise OSError('Repository unavailable')
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(evidence.os, 'open', unavailable_root)
    value = counts(inspect(tmp_path, repo))
    assert value['listing_complete'] is True
    assert value['eligible_files'] == value['not_attempted_files'] == 1
    assert value['attempted_files'] == value['read_files'] == 0


def test_symlink_and_invalid_utf8_are_failed_reads(tmp_path):
    repo = repository(tmp_path)
    (repo / 'binary.py').write_bytes(b'\xff')
    outside = tmp_path / 'outside.py'
    outside.write_text('class Outside: pass\n')
    (repo / 'link.py').symlink_to(outside)
    value = counts(inspect(tmp_path, repo))
    assert value['eligible_files'] == value['attempted_files'] == value['failed_reads'] == 2
    assert value['read_files'] == 0
