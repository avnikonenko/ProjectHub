"""Dependency references retain complete declarations through evidence budgeting."""
import pytest

from backend.app.integrations.codex import scheme
from backend.app.integrations.codex import scheme_evidence as evidence
from backend.tests.integrations.test_scheme_evidence import repository, setup


@pytest.mark.parametrize('manifest', ['env.yml', 'requirements.txt', 'pyproject.toml'])
def test_long_manifests_keep_every_dependency_in_its_cited_chunk(tmp_path, manifest):
    repo = repository(tmp_path)
    packages = [f'package-with-a-deliberately-long-name-{index:03d}==1.0' for index in range(90)]
    if manifest == 'env.yml':
        body = 'dependencies:\n  - pip:\n' + ''.join(f'    - {package}\n' for package in packages)
    elif manifest == 'pyproject.toml':
        body = '[project]\nname="sample"\ndependencies=[' + ','.join(f'"{package}"' for package in packages) + ']\n'
    else:
        body = '\n'.join(packages)
    (repo / manifest).write_text(body)
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        assert len(result['dependencies']) == 90
        sources = {source['id']: source for source in result['sources']}
        chunks = [source for source in result['sources'] if source.get('path') == manifest]
        assert len(chunks) > 1
        assert all(len(source['text']) <= 3200 for source in chunks)
        for item in result['dependencies']:
            line = f"{item['name']} {item['constraint']} ({item['category']})"
            assert line in sources[item['source_ids'][0]]['text'].splitlines()
            assert sources[item['source_ids'][0]]['path'] == manifest
        assert result['coverage']['file_counts']['evidence_files'] == 1
        replay = evidence.collect_evidence(engine, 1)
        assert replay['diagnostics']['cache_hits'] == 1
        assert replay['dependencies'] == result['dependencies']
    finally:
        engine.dispose()


def test_omitted_manifest_chunks_cannot_leave_dangling_dependency_records(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'requirements.txt').write_text('\n'.join(
        f'package-with-a-deliberately-long-name-{index:03d}==1.0' for index in range(90)))
    monkeypatch.setattr(evidence, 'scheme_inspection_limits', lambda: (3300, 2 * 1024 * 1024, 140_000))
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        assert 0 < len(result['dependencies']) < 90
        sources = {source['id']: source for source in result['sources']}
        for item in result['dependencies']:
            assert all(ref in sources for ref in item['source_ids'])
            assert f"{item['name']} {item['constraint']} ({item['category']})" in sources[item['source_ids'][0]]['text'].splitlines()
        assert result['coverage']['omitted_reason_counts']['source_output_budget'] > 0
        assert len(result['dependency_inventory']['dependencies']) == 90
    finally:
        engine.dispose()


def test_inventory_keeps_late_conda_and_pip_packages_and_checks_all_python_files(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'project_env.yml').write_text(
        'dependencies:\n  - _libgcc_mutex=0.1=main\n'
        + ''.join(f'  - package-{number:03d}=1\n' for number in range(520))
        + '  - pip:\n    - crem==0.2.17\n    - -r extras.in\n')
    (repo / 'extras.in').write_text('included>=1\n')
    (repo / 'main.py').write_text('import local_helper\n')
    (repo / 'local_helper.py').write_text('import os\n')
    (repo / 'tests').mkdir()
    (repo / 'tests' / 'test_extra.py').write_bytes(
        b'# coding: latin-1\n# \xe9\n' + b'# padding\n' * 7100
        + b'def load():\n    from crem.crem import grow_mol\n    import import_only\n')
    monkeypatch.setattr(evidence, 'scheme_inspection_limits', lambda: (3500, 2 * 1024 * 1024, 140_000))
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        inventory = result['dependency_inventory']
        rows = {row['name']: row for row in inventory['dependencies']}
        assert len(rows) == 524
        assert rows['crem']['constraint'] == '==0.2.17'
        assert rows['_libgcc_mutex']['constraint'] == '=0.1=main'
        assert rows['included']['constraint'] == '>=1'
        assert rows['import_only']['category'] == 'import:python'
        assert 'local_helper' not in rows and 'os' not in rows
        assert inventory['coverage'] == {'files_total': 5, 'files_scanned': 5, 'limitations': []}
        sources = {source['id']: source for source in inventory['sources']}
        for row in rows.values():
            assert row['name'] in sources[row['source_ids'][0]]['text']
        assert len(result['dependencies']) <= 100
        assert len({row['id'] for row in inventory['dependencies']}) == len(rows)
        assert evidence.collect_evidence(engine, 1)['dependency_inventory'] == inventory
    finally:
        engine.dispose()


def test_inventory_attaches_only_explanations_with_available_references():
    inventory = {'dependencies': [{'id': 'a'}, {'id': 'b'}, {'id': 'c'}], 'sources': []}
    explained = [{'id': 'a', 'purpose': 'Used here.', 'purpose_source_ids': ['code']},
                 {'id': 'b', 'purpose': 'Missing citation.', 'purpose_source_ids': ['missing']}]
    merged = scheme.inventory_with_purposes(inventory, explained, [{'id': 'code'}])
    assert merged['dependencies'] == [explained[0], {'id': 'b'}, {'id': 'c'}]
    assert inventory['dependencies'][0] == {'id': 'a'}
