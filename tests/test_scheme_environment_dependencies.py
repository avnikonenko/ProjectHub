"""Static, bounded environment dependency evidence; no YAML/package execution."""
import json

import pytest

from backend.app.integrations.codex import scheme_evidence as evidence
from backend.app.integrations.codex.scheme import validate_output
from backend.app.integrations.codex.scheme_environments import environment_dependencies, is_environment_manifest
from backend.tests.integrations.test_scheme_evidence import repository, setup


@pytest.mark.parametrize('name', ['env.yml', 'env_gpu.yml', 'environment.yaml', 'environment-dev.yml', 'conda.yml'])
def test_environment_names_are_manifest_candidates(name):
    assert is_environment_manifest(name)
    assert evidence._manifest(name)


@pytest.mark.parametrize('name', ['deploy.yml', 'env.txt', 'environment.json', 'secrets.yaml'])
def test_unrelated_yaml_is_not_an_environment_manifest(name):
    assert not is_environment_manifest(name)


def test_conda_constraints_channels_quotes_and_nested_pip():
    limitations = set()
    parsed = environment_dependencies('''name: md
channels:
  - conda-forge
dependencies:
  - python=3.11
  - gromacs=2025.3=nompi_h26635d9_101
  - conda-forge::rdkit>=2024.09,<2026 # bounded
  - "numpy>=1.26,<2"
  - 'pandas>=1.5,<2' # retain constraint
  - pip
  - pip:
    - mdanalysis==2.10.0
    - prolif==2.1.0
    - gmx-mmpbsa==1.6.5
variables:
  PRIVATE_KEY: NEVER_INCLUDE
''', limitations)
    assert not limitations
    records = {name: (constraint, category) for name, constraint, category in parsed}
    assert records['gromacs'] == ('=2025.3=nompi_h26635d9_101', 'environment:conda')
    assert records['rdkit'] == ('>=2024.09,<2026', 'environment:conda')
    assert records['numpy'] == ('>=1.26,<2', 'environment:conda')
    assert records['pandas'] == ('>=1.5,<2', 'environment:conda')
    assert records['mdanalysis'] == ('==2.10.0', 'environment:pip')
    assert records['gmx-mmpbsa'] == ('==1.6.5', 'environment:pip')
    assert 'PRIVATE_KEY' not in records


def test_environment_evidence_is_sanitized_and_variant_sources_remain_distinct(tmp_path):
    repo = repository(tmp_path)
    body = '''name: private
channels:
  - https://user:SECRET_CHANNEL@example.test/conda
dependencies:
  - gromacs=2025.3=BUILD
  - ambertools=24.8
  - pip:
    - prolif==2.1.0
    - private-package @ https://user:SECRET_PIP@example.test/pkg.whl
variables:
  TOKEN: SECRET_VARIABLE
prefix: /private/machine/SECRET_PREFIX
'''
    (repo / 'env.yml').write_text(body.replace('BUILD', 'nompi_cpu'))
    (repo / 'env_gpu.yml').write_text(body.replace('BUILD', 'nompi_cuda'))
    (repo / 'requirements-dev.txt').write_text('pytest>=8\n')
    engine = setup(tmp_path, repo)
    try:
        first = evidence.collect_evidence(engine, 1)
        gromacs = [d for d in first['dependencies'] if d['name'] == 'gromacs']
        assert len(gromacs) == 2
        assert {d['constraint'] for d in gromacs} == {'=2025.3=nompi_cpu', '=2025.3=nompi_cuda'}
        assert len({d['id'] for d in gromacs}) == 2
        assert len({d['source_ids'][0] for d in gromacs}) == 2
        sources = {source['id']: source for source in first['sources']}
        assert {sources[d['source_ids'][0]]['path'] for d in gromacs} == {'env.yml', 'env_gpu.yml'}
        serialized = json.dumps(first)
        assert 'SECRET_' not in serialized
        assert 'Direct/local reference (location omitted)' in serialized
        assert all(source['provenance'] == 'dependency manifest' for source in sources.values() if source.get('path', '').endswith('.yml'))
        assert evidence.collect_evidence(engine, 1)['diagnostics']['cache_hits'] == 1
        (repo / 'env.yml').write_text(body.replace('BUILD', 'changed_cpu'))
        changed = evidence.collect_evidence(engine, 1)
        assert changed['diagnostics']['cache_hits'] == 0
        assert changed['fingerprint'] != first['fingerprint']
    finally:
        engine.dispose()


@pytest.mark.parametrize('body', [
    'dependencies: [numpy, scipy]\n',
    'dependencies: &packages\n  - numpy\n',
    'dependencies: *packages\n',
    'dependencies:\n  - !!python/object/apply:os.system [echo unsafe]\n',
    'dependencies:\n  - other-installer:\n    - numpy\n',
    'dependencies:\n  - numpy\n---\ndependencies:\n  - scipy\n',
    'dependencies:\n  - numpy\ndependencies:\n  - scipy\n',
    'dependencies:\n  - pip:\n    - -r requirements.txt\n    - !!python/object:example\n',
    'dependencies:\n  - numpy # [linux]\n',
    'dependencies:\n  - pip:\n    - other-installer:\n      - bogus>=1\n',
])
def test_unsupported_yaml_is_reported_without_interpretation(body):
    limitations = set()
    assert environment_dependencies(body, limitations) == []
    assert limitations


def test_unsupported_installer_does_not_hide_following_valid_package():
    limitations = set()
    assert environment_dependencies('''dependencies:
  - other-installer:
    - bogus
  - ambertools=24.8
  - pip:
    - unsupported: value
    - prolif==2.1.0
''', limitations) == [
        ('ambertools', '=24.8', 'environment:conda'),
        ('prolif', '==2.1.0', 'environment:pip'),
    ]
    assert limitations


def test_unindented_yaml_lists_and_following_sections():
    limitations = set()
    assert environment_dependencies('''dependencies:
- numpy>=1
- pip:
  - scipy>=2
channels:
- not-a-dependency
''', limitations) == [('numpy', '>=1', 'environment:conda'), ('scipy', '>=2', 'environment:pip')]
    assert not limitations


@pytest.mark.parametrize(('path', 'category'), [
    ('requirements-ci.txt', 'development'),
    ('requirements-dev.txt', 'development'),
    ('requirements-test.txt', 'development'),
    ('requirements-release.txt', 'build'),
    ('requirements-build.txt', 'build'),
    ('docs/requirements.txt', 'development:documentation'),
    ('requirements-docs.txt', 'development:documentation'),
    ('requirements.txt', 'runtime'),
    ('device/requirements.txt', 'runtime'),
    ('contest/requirements.txt', 'runtime'),
])
def test_requirements_categories_follow_file_purpose_not_package_name(path, category):
    dependencies = []
    evidence._requirements('example-package>=1\n', path, 'source:one', dependencies, [], set())
    assert dependencies[0]['category'] == category


@pytest.mark.parametrize('entry', [
    'package[extra] @ /private/SECRET.whl',
    'package[extra]@relative/SECRET.whl',
    'package[extra] @ ~/private/SECRET.whl',
    'package[extra] @ https://user:SECRET@example.test/package.whl',
    'package @ /private/SECRET.whl',
    'package@relative/SECRET.whl',
])
@pytest.mark.parametrize('path', ['env.yml', 'requirements.txt', 'pyproject.toml'])
def test_direct_reference_redaction_survives_extras_and_compact_at(entry, path):
    value = ('dependencies:\n  - pip:\n    - ' + entry + '\n' if path == 'env.yml'
             else '[project]\ndependencies = [' + json.dumps(entry) + ']\n'
             if path == 'pyproject.toml' else entry + '\n')
    dependencies = []
    evidence._parse_manifest(value, path, 'source:one', dependencies, [], set())
    assert len(dependencies) == 1
    assert dependencies[0]['constraint'] == 'Direct/local reference (location omitted)'
    assert 'SECRET' not in json.dumps(dependencies)


@pytest.mark.parametrize('entry', [
    'package~=1.2',
    'package[extra]~=1.2',
    'package>=1; python_version < "3.14"',
    'package[extra]>=1; python_version < "3.14"',
])
@pytest.mark.parametrize('path', ['env.yml', 'requirements.txt', 'pyproject.toml'])
def test_standard_constraints_and_markers_are_not_direct_references(entry, path):
    value = ('dependencies:\n  - pip:\n    - ' + entry + '\n' if path == 'env.yml'
             else '[project]\ndependencies = [' + json.dumps(entry) + ']\n'
             if path == 'pyproject.toml' else entry + '\n')
    dependencies = []
    evidence._parse_manifest(value, path, 'source:one', dependencies, [], set())
    assert len(dependencies) == 1
    assert dependencies[0]['constraint'] == entry.removeprefix('package')


@pytest.mark.parametrize('path', ['env.yml', 'requirements.txt'])
def test_exact_duplicates_collapse_but_distinct_constraints_keep_stable_identity(tmp_path, path):
    repo = repository(tmp_path)
    declarations = ['example>=1', 'example<2', 'example>=1']

    def write(entries):
        value = ('dependencies:\n' + ''.join('  - ' + entry + '\n' for entry in entries)
                 if path == 'env.yml' else '\n'.join(entries) + '\n')
        (repo / path).write_text(value)

    write(declarations)
    engine = setup(tmp_path, repo)
    try:
        first = evidence.collect_evidence(engine, 1)
        assert len(first['dependencies']) == 2
        identifiers = {item['constraint']: item['id'] for item in first['dependencies']}
        assert len(set(identifiers.values())) == 2
        assert set(identifiers) == {'>=1', '<2'}
        graph = {'nodes': [], 'edges': [], 'explanation': 'No graph evidence.'}
        response = {'architecture': graph, 'workflow': graph, 'dependencies': [
            {'id': item['id'], 'purpose': 'Explanation for ' + item['constraint'],
             'source_ids': item['source_ids']} for item in first['dependencies']]}
        _, _, explained = validate_output(json.dumps(response), first)
        assert {item['constraint']: item['purpose'] for item in explained} == {
            '>=1': 'Explanation for >=1', '<2': 'Explanation for <2'}
        write(list(reversed(declarations[:-1])))
        changed = evidence.collect_evidence(engine, 1)
        assert changed['diagnostics']['cache_hits'] == 0
        assert {item['constraint']: item['id'] for item in changed['dependencies']} == identifiers
    finally:
        engine.dispose()


def test_matching_environment_constraints_keep_independent_variant_sources(tmp_path):
    repo = repository(tmp_path)
    for path in ('env.yml', 'env_gpu.yml'):
        (repo / path).write_text('dependencies:\n  - example>=1\n')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        assert len(result['dependencies']) == 2
        assert len({item['id'] for item in result['dependencies']}) == 2
        sources = {source['id']: source for source in result['sources']}
        assert {sources[item['source_ids'][0]]['path'] for item in result['dependencies']} == {'env.yml', 'env_gpu.yml'}
    finally:
        engine.dispose()


def test_distinct_constraints_and_exact_duplicates_have_correct_chunk_evidence(tmp_path):
    repo = repository(tmp_path)
    name = 'example-package-' + 'x' * 60
    declarations = [name + '>=' + str(index) for index in range(60)]
    (repo / 'requirements.txt').write_text('\n'.join(declarations + declarations) + '\n')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        assert len(result['dependencies']) == 60
        assert len({item['id'] for item in result['dependencies']}) == 60
        sources = {source['id']: source for source in result['sources']}
        assert len({item['source_ids'][0] for item in result['dependencies']}) > 1
        for item in result['dependencies']:
            assert f"{item['name']} {item['constraint']} ({item['category']})" in sources[item['source_ids'][0]]['text']
    finally:
        engine.dispose()
