"""Saved Scheme inventory counts from real local Git/files; no model calls."""
import json
import os
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
        omissions = result['coverage']['file_omissions']
        assert omissions['total'] == value['eligible_files'] - value['evidence_files']
        assert len(omissions['files']) == min(omissions['total'], evidence._MAX_RECORDED_FILE_OMISSIONS)
        assert len({row['path'] for row in omissions['files']}) == len(omissions['files'])
        assert {row['path'] for row in omissions['files']}.isdisjoint(
            source['path'] for source in result['sources'] if source.get('path'))
    else:
        assert 'file_omissions' not in result['coverage']
    return value


def omissions(result):
    return {row['path']: row['reason'] for row in result['coverage']['file_omissions']['files']}


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
    assert omissions(result) == {}
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
    assert omissions(result) == {'binary.py': 'unreadable_or_malformed', 'empty.py': 'no_usable_excerpt',
                                 'pyproject.toml': 'unreadable_or_malformed'}


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
        assert omissions(result) == {'module1.py': 'metadata_byte_budget', 'module2.py': 'metadata_byte_budget'}
    else:
        assert value['evidence_files'] == 0
        reason = 'byte_budget' if limit == 'bytes' else 'time_budget'
        assert omissions(result) == {f'module{i}.py': reason for i in range(3)}
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
        assert set(omissions(result).values()) == {'package_output_budget'}
        payload = {key: result[key] for key in ('sources', 'dependencies', 'tree', 'coverage', 'knowledge_context')}
        payload['coverage'] = {key: item for key, item in payload['coverage'].items() if key != 'file_omissions'}
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
    monkeypatch.setattr(evidence, 'scheme_source_chars', lambda: 0)
    result = inspect(tmp_path, repo)
    value = counts(result)
    assert value['read_files'] == value['read_not_in_evidence'] == 1
    assert value['evidence_files'] == 0
    assert result['coverage']['omitted_reason_counts']['source_output_budget'] > 0
    assert omissions(result) == {'main.py': 'source_output_budget'}


def test_configured_excerpt_budget_expands_file_coverage_and_scopes_the_cache(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'docs').mkdir()
    for index in range(55):
        (repo / 'docs' / f'feature{index:02}.md').write_text(
            f'# Feature {index}\n\n' + 'Describe the application workflow and output files.\n' * 28)
    engine = setup(tmp_path, repo)
    try:
        monkeypatch.setenv('PROJECTHUB_SCHEME_SOURCE_CHARS', '40000')
        baseline = evidence.collect_evidence(engine, 1)
        assert baseline['coverage']['inspection_policy']['max_source_chars'] == 40_000
        assert 'source_output_budget' in omissions(baseline).values()
        assert evidence.collect_evidence(engine, 1)['diagnostics']['cache_hits'] == 1

        monkeypatch.setenv('PROJECTHUB_SCHEME_SOURCE_CHARS', '80000')
        expanded = evidence.collect_evidence(engine, 1)
        assert expanded['coverage']['inspection_policy']['max_source_chars'] == 80_000
        assert expanded['diagnostics']['cache_hits'] == 0
        assert counts(expanded)['evidence_files'] > counts(baseline)['evidence_files']
        assert 40_000 < expanded['diagnostics']['input_chars'] <= 80_000
        assert expanded['diagnostics']['package_chars'] <= evidence._MAX_PACKAGE_CHARS
        assert (expanded['coverage']['inspection_policy']['max_read_bytes']
                == baseline['coverage']['inspection_policy']['max_read_bytes'])
        assert expanded['fingerprint'] != baseline['fingerprint']
        assert evidence.collect_evidence(engine, 1)['diagnostics']['cache_hits'] == 1
    finally:
        engine.dispose()


def test_manifest_with_one_retained_excerpt_is_not_a_skipped_file(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'requirements.txt').write_text(''.join(f'package{i:03}{"x" * 100}==1.0\n' for i in range(60)))
    monkeypatch.setattr(evidence, 'scheme_source_chars', lambda: 3300)
    result = inspect(tmp_path, repo)
    value = counts(result)
    assert value['eligible_files'] == value['evidence_files'] == 1
    assert sum(source.get('path') == 'requirements.txt' for source in result['sources']) == 1
    assert result['coverage']['omitted_reason_counts']['source_output_budget'] > 0
    assert omissions(result) == {}


def test_file_list_is_bounded_but_total_remains_exact(tmp_path):
    repo = repository(tmp_path)
    file_count = evidence._MAX_RECORDED_FILE_OMISSIONS + 4
    for index in range(file_count):
        (repo / f'module{index:03}.py').touch()
    result = inspect(tmp_path, repo)
    assert counts(result)['eligible_files'] == file_count
    assert result['coverage']['file_omissions']['total'] == file_count
    assert list(omissions(result)) == [f'module{i:03}.py' for i in range(evidence._MAX_RECORDED_FILE_OMISSIONS)]


def test_repository_disappearing_after_listing_preserves_known_inventory_without_reads(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('class Main: pass\n')
    original_open = evidence.os.open

    def unavailable_root(path, *args, **kwargs):
        if path == repo:
            raise OSError('Repository unavailable')
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(evidence.os, 'open', unavailable_root)
    result = inspect(tmp_path, repo)
    value = counts(result)
    assert value['listing_complete'] is True
    assert value['eligible_files'] == value['not_attempted_files'] == 1
    assert value['attempted_files'] == value['read_files'] == 0
    assert omissions(result) == {'main.py': 'unsafe_or_unavailable'}


def test_symlink_and_invalid_utf8_are_failed_reads(tmp_path):
    repo = repository(tmp_path)
    (repo / 'binary.py').write_bytes(b'\xff')
    outside = tmp_path / 'outside.py'
    outside.write_text('class Outside: pass\n')
    (repo / 'link.py').symlink_to(outside)
    result = inspect(tmp_path, repo)
    value = counts(result)
    assert value['eligible_files'] == value['attempted_files'] == value['failed_reads'] == 2
    assert value['read_files'] == 0
    assert omissions(result) == {'binary.py': 'unreadable_or_malformed', 'link.py': 'unsafe_or_unavailable'}


def test_non_utf8_git_filename_is_excluded_from_serializable_coverage(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('class Main: pass\n')
    (repo / 'feature-é.py').touch()
    (repo / 'ab').mkdir()
    (repo / os.fsdecode(b'ab/\xff.py')).touch()
    monkeypatch.setattr(evidence, '_MAX_PATHS', 1)

    result = inspect(tmp_path, repo)
    # Starlette encodes JSON with ensure_ascii disabled and strict UTF-8.
    json.dumps(result, ensure_ascii=False).encode('utf-8')
    value = counts(result)
    assert value['repository_paths_total'] == 3
    assert value['excluded_paths'] == 1
    assert omissions(result) == {'feature-é.py': 'no_usable_excerpt'}
