"""Explicit imports can promote safe code beyond production-priority paths."""
import json

from backend.app.integrations.codex import scheme_evidence as evidence
from backend.tests.integrations.test_scheme_evidence import repository, setup


def test_imported_archive_module_and_nested_imports_are_inspected(tmp_path):
    repo = repository(tmp_path)
    (repo / 'archives').mkdir()
    (repo / 'main.py').write_text('from archives.runner import run\ndef main(): return run()\n')
    (repo / 'archives' / 'runner.py').write_text(
        'from .worker import compute\ndef run(): return compute()\n')
    (repo / 'archives' / 'worker.py').write_text(
        'def compute(): return significant_processing()\n')
    (repo / 'archives' / 'unreferenced.py').write_text(
        'def obsolete(): return outdated_processing()\n')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        sources = {source.get('path'): source for source in result['sources']}
        assert {'main.py', 'archives/runner.py', 'archives/worker.py'} <= sources.keys()
        assert 'significant_processing' in sources['archives/worker.py']['text']
        assert 'archives/unreferenced.py' not in sources
        assert not evidence._production_path('archives/runner.py')
        assert any(item['path'] == 'archives/unreferenced.py' for item in result['tree'])
        counts = result['coverage']['file_counts']
        assert counts['eligible_files'] == counts['attempted_files'] == 3
        assert counts['evidence_files'] == 3
    finally:
        engine.dispose()


def test_promoted_imports_cannot_bypass_exclusions_or_symlink_reads(tmp_path):
    repo = repository(tmp_path)
    (repo / 'archives').mkdir()
    (repo / 'main.py').write_text(
        'from archives.ignored import run\nfrom archives.settings import configure\n'
        'from archives.link import process\ndef main(): return run()\n')
    (repo / '.gitignore').write_text('archives/ignored.py\n')
    (repo / 'archives' / 'ignored.py').write_text('def run(): return EXCLUDED_SECRET\n')
    (repo / 'archives' / 'settings.py').write_text('def configure(): return PRIVATE_SECRET\n')
    outside = tmp_path / 'outside.py'
    outside.write_text('def process(): return SYMLINK_SECRET\n')
    (repo / 'archives' / 'link.py').symlink_to(outside)
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        paths = {source.get('path') for source in result['sources']}
        assert 'main.py' in paths
        assert not paths & {'archives/ignored.py', 'archives/settings.py', 'archives/link.py'}
        serialized = json.dumps(result)
        assert all(secret not in serialized for secret in (
            'EXCLUDED_SECRET', 'PRIVATE_SECRET', 'SYMLINK_SECRET'))
        assert result['coverage']['freshness_unknown']
    finally:
        engine.dispose()
