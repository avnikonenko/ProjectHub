"""Dependency references retain complete declarations through evidence budgeting."""
import pytest

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
    finally:
        engine.dispose()
