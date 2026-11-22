"""Declared packaging entrypoints survive bounded, read-only Scheme inspection."""
import json

from backend.app.integrations.codex import scheme_evidence as evidence
from backend.tests.integrations.test_scheme_evidence import repository, setup


def crowded_repository(tmp_path, *, pyproject=False):
    repo = repository(tmp_path)
    package = repo / 'sample'
    package.mkdir()
    for index in range(65):
        (package / f'aaa_helper_{index:02d}.py').write_text(''.join(
            f'def helper_{number}():\n    return utility_{number}()\n' for number in range(60)))
    for name in ('zeta_prepare', 'zeta_report'):
        (package / f'{name}.py').write_text(f'from .zz_{name} import {name}_pipeline\n'
            f'def main():\n    if requested():\n        return {name}_pipeline()\n    return fallback()\n')
        (package / f'zz_{name}.py').write_text(f'def {name}_pipeline():\n    return process_output()\n')
    if pyproject:
        (repo / 'pyproject.toml').write_text('[project]\nname="sample"\n'
            '[project.scripts]\nprepare="sample.zeta_prepare:main"\nreport="sample.zeta_report:main"\n')
    else:
        (repo / 'setup.py').write_text('import setuptools as packaging\n'
            'raise RuntimeError("MUST_NOT_EXECUTE")\n'
            'packaging.setup(entry_points={"console_scripts": ['
            '"prepare = sample.zeta_prepare:main", "report = sample.zeta_report:main"]})\n')
    return repo


def test_multiple_setup_entrypoints_survive_crowded_package_and_cache(tmp_path, monkeypatch):
    repo = crowded_repository(tmp_path)
    engine = setup(tmp_path, repo)
    first = evidence.collect_evidence(engine, 1)
    paths = {source.get('path'): source for source in first['sources']}
    for name in ('zeta_prepare', 'zeta_report'):
        assert f'sample/{name}.py' in paths
        assert f'{name}_pipeline' in paths[f'sample/{name}.py']['text']
        assert f'sample/zz_{name}.py' in paths
    assert 'Declared entrypoint report = sample.zeta_report:main' in paths['setup.py']['text']
    assert paths['setup.py']['provenance'] == 'observed in code'
    assert 'MUST_NOT_EXECUTE' not in json.dumps(first)
    assert first['coverage']['files_inspected'] > 24
    assert first['diagnostics']['bytes_read'] <= evidence.DEFAULT_SCHEME_READ_BYTES
    monkeypatch.setattr(evidence, '_read', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Cache reread')))
    cached = evidence.collect_evidence(engine, 1)
    assert cached['diagnostics']['cache_hits'] == 1
    assert cached['fingerprint'] == first['fingerprint']
    engine.dispose()


def test_pyproject_entrypoints_survive_source_budget_and_keep_whole_excerpts(tmp_path, monkeypatch):
    repo = crowded_repository(tmp_path, pyproject=True)
    engine = setup(tmp_path, repo)
    original = evidence._records

    def large_records(db, project):
        _, markers, limitations, count = original(db, project)
        records = [evidence._source(f'note:{index}', 'Large note', str(index) * 5900, 'project note')
                   for index in range(6)]
        return records, markers, limitations, count

    monkeypatch.setattr(evidence, '_records', large_records)
    result = evidence.collect_evidence(engine, 1)
    paths = {source.get('path'): source for source in result['sources']}
    assert {'sample/zeta_prepare.py', 'sample/zeta_report.py', 'pyproject.toml'} <= paths.keys()
    assert sum(len(source['text']) for source in result['sources']) <= evidence.scheme_inspection_limits()[0]
    assert any(source['id'].startswith('note:') for source in result['sources'])
    assert all(len(source['text']) == 5900 for source in result['sources'] if source['id'].startswith('note:'))
    assert any('source excerpt budget' in value for value in result['coverage']['limitations'])
    engine.dispose()


def test_only_literal_imported_setup_calls_are_declarations():
    source = '''from setuptools import setup as install
unused = {"console_scripts": ["unused = sample.hidden:main"]}
fake.setup(entry_points={"console_scripts": ["fake = sample.fake:main"]})
install(entry_points=unused)
install(entry_points={"console_scripts": ["good = sample.good:main", dynamic(),
    "escape = ../outside:main", "url = https://secret.test:main", "bad = sample.good:main()"]})
'''
    assert evidence._entrypoint_declarations(source, 'setup.py') == [('good', 'sample.good:main')]


def test_excluded_and_symlink_entrypoints_cannot_supply_evidence(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'setup.py').write_text('from setuptools import setup\nsetup(entry_points={"console_scripts": ['
        '"hidden = hidden:main", "linked = linked:main", "escape = outside:main"]})\n')
    (repo / 'hidden.py').write_text('def forbidden_hidden(): pass\n')
    outside = tmp_path / 'outside.py'
    outside.write_text('def forbidden_external(): pass\n')
    (repo / 'linked.py').symlink_to(outside)
    monkeypatch.setenv('PROJECTHUB_EVIDENCE_EXCLUDE', '["hidden.py"]')
    engine = setup(tmp_path, repo)
    result = evidence.collect_evidence(engine, 1)
    serialized = json.dumps(result)
    assert 'forbidden_hidden' not in serialized and 'forbidden_external' not in serialized
    assert not {'hidden.py', 'linked.py', 'outside.py'} & {s.get('path') for s in result['sources']}
    assert result['coverage']['partial']
    engine.dispose()


def test_initializer_entrypoint_is_structural_and_malformed_setup_is_safe(tmp_path):
    repo = repository(tmp_path)
    (repo / 'sample').mkdir()
    (repo / 'sample/__init__.py').write_text('SECRET = "PRIVATE_INITIALIZER_LITERAL"\n'
        'def main():\n    return run_pipeline("PRIVATE_ARGUMENT")\n')
    (repo / 'pyproject.toml').write_text('[project.scripts]\nstart="sample:main"\n')
    (repo / 'setup.py').write_text('from setuptools import setup\nsetup(\n')
    engine = setup(tmp_path, repo)
    result = evidence.collect_evidence(engine, 1)
    source = next(source for source in result['sources'] if source.get('path') == 'sample/__init__.py')
    assert source['provenance'] == 'observed in code'
    assert 'run_pipeline' in source['text']
    assert 'PRIVATE_' not in json.dumps(result)
    engine.dispose()


def test_feature_documentation_survives_badges_with_original_wording(tmp_path):
    repo = repository(tmp_path)
    badges = '[![Badge](https://example.test/badge)](https://example.test/project)\n' * 35
    feature_section = '## Features\n- Prepare an input\n- Compute an ensemble\n- Export report files\n'
    (repo / 'README.md').write_text('# Sample pipeline\n\n' + badges + '\n'
        'A tool for processing an ensemble.\n\n' + feature_section + '\n## Usage\n' + 'Unrelated commands.\n' * 100)
    engine = setup(tmp_path, repo)
    result = evidence.collect_evidence(engine, 1)
    source = next(source for source in result['sources'] if source.get('path') == 'README.md')
    assert feature_section.rstrip() in source['text']
    assert 'A tool for processing an ensemble.' in source['text'] and '# Sample pipeline' in source['text']
    assert len(source['text']) <= evidence._MAX_EXCERPT
    assert any('documentation sections' in value for value in result['coverage']['limitations'])
    engine.dispose()


def test_general_scheme_finds_long_documented_entrypoint_and_its_called_modules(tmp_path):
    repo = repository(tmp_path)
    (repo / 'README.md').write_text('# Pipeline\n\nRun `python pipeline.py` to produce a report.\n')
    (repo / 'pipeline.py').write_text('import execution\nimport crossover\n'
        + '# padding\n' * 7000
        + 'def main():\n    execution.restore_checkpoint()\n    execution.write_report()\n\n'
          'if __name__ == "__main__":\n    main()\n')
    (repo / 'execution.py').write_text('def restore_checkpoint(): pass\ndef write_report(): pass\n')
    (repo / 'crossover.py').write_text('def dormant_helper(): pass\n')
    engine = setup(tmp_path, repo)
    result = evidence.collect_evidence(engine, 1)
    sources = {source.get('path'): source for source in result['sources']}
    assert 'Python entrypoint flow main' in sources['pipeline.py']['text']
    assert 'execution.restore_checkpoint' in sources['pipeline.py']['text']
    assert 'execution.write_report' in sources['pipeline.py']['text']
    assert list(sources).index('execution.py') < list(sources).index('crossover.py')
    assert result['diagnostics']['bytes_read'] <= evidence.DEFAULT_SCHEME_READ_BYTES
    engine.dispose()


def test_general_scheme_includes_referenced_configuration_with_its_active_sections(tmp_path):
    repo = repository(tmp_path)
    (repo / 'pyproject.toml').write_text('[project.scripts]\nprepare="prepare:main"\nanalyze="analyze:main"\n')
    (repo / 'prepare.py').write_text('def main():\n    return prepare_input()\n')
    (repo / 'analyze.py').write_text('def main():\n    return load_template("methods.in")\n')
    (repo / 'scripts').mkdir()
    (repo / 'scripts/methods.in').write_text('&active\n key="PRIVATE_VALUE"\n/\n#&optional\n#/\n')
    (repo / 'tests').mkdir()
    (repo / 'tests/methods.in').write_text('&fixture\n/\n')
    (repo / 'unused.in').write_text('&unused\n/\n')
    engine = setup(tmp_path, repo)
    result = evidence.collect_evidence(engine, 1)
    sources = {source.get('path'): source for source in result['sources']}
    assert {'prepare.py', 'analyze.py', 'scripts/methods.in'} <= sources.keys()
    config = sources['scripts/methods.in']
    assert config['provenance'] == 'repository configuration'
    assert 'L1: Active namelist section &active' in config['text']
    assert 'L4: Commented-out namelist section &optional' in config['text']
    assert {'unused.in', 'tests/methods.in'}.isdisjoint(sources)
    assert 'PRIVATE_VALUE' not in json.dumps(result)
    engine.dispose()


def test_mixed_resolved_and_missing_entrypoints_report_partial_coverage(tmp_path):
    repo = repository(tmp_path)
    (repo / 'pyproject.toml').write_text('[project.scripts]\n'
        'present="present:main"\nmissing="missing:main"\n')
    (repo / 'present.py').write_text('def main():\n    return process_input()\n')
    engine = setup(tmp_path, repo)
    result = evidence.collect_evidence(engine, 1)
    assert any(source.get('path') == 'present.py' for source in result['sources'])
    assert any('declared entrypoint modules were unavailable' in limitation
               for limitation in result['coverage']['limitations'])
    assert result['coverage']['partial']
    engine.dispose()


def test_feature_section_keeps_nested_subheadings_until_peer_section():
    section = ('## Features\n\n### Preparation\nPrepare complete inputs.\n\n'
               '### Reporting\nExport complete reports.\n')
    excerpt, omitted = evidence._documentation_excerpt(
        '# Sample\n\n' + section + '\n## Installation\nInstall unrelated packages.\n')
    assert section.rstrip() in excerpt
    assert '## Installation' not in excerpt and 'Install unrelated packages.' not in excerpt
    assert omitted


def test_general_scheme_reserves_matching_index_writer_and_query(tmp_path, monkeypatch):
    repo = crowded_repository(tmp_path)
    (repo / 'app.py').write_text(
        'from z_search_results import query_records\n'
        '@app.post("/records/import")\ndef load_records():\n'
        '    db.add(Record())\n    db.commit()\n'
        '@app.get("/records/search")\ndef find_records():\n    return query_records()\n'
    )
    (repo / 'z_search_results.py').write_text(
        'def query_records():\n'
        '    return db.execute(text("SELECT rowid FROM records_fts WHERE records_fts MATCH :query"))\n'
    )
    migrations = repo / 'migrations' / 'versions'
    migrations.mkdir(parents=True)
    (migrations / '001_index.py').write_text(
        'from alembic import op\n'
        'def upgrade():\n'
        '    op.execute("CREATE VIRTUAL TABLE records_fts USING fts5(content)")\n'
        '    op.execute("CREATE TRIGGER records_insert AFTER INSERT ON records BEGIN "'
        '"INSERT INTO records_fts(rowid,content) VALUES(new.id,new.content); END")\n'
    )
    engine = setup(tmp_path, repo)

    def records(db, project):
        notes = [evidence._source(f'note:{index}', 'Long discussion', str(index) * 5600, 'project note')
                 for index in range(8)]
        return notes, [], [], 0

    monkeypatch.setattr(evidence, '_records', records)
    result = evidence.collect_evidence(engine, 1)
    by_path = {source.get('path'): source['text'] for source in result['sources']}
    assert 'records_insert AFTER INSERT ON records: INSERT INTO records_fts' in by_path['migrations/versions/001_index.py']
    assert 'records_fts MATCH' in by_path['z_search_results.py']
    assert '/records/import' in by_path['app.py'] and 'db.commit' in by_path['app.py']
    assert '/records/search' in by_path['app.py'] and 'query_records' in by_path['app.py']
    assert 'sample/zeta_prepare.py' in by_path
    assert sum(len(source['text']) for source in result['sources']) <= evidence.scheme_inspection_limits()[0]
    engine.dispose()
