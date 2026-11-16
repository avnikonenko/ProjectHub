"""Scheme uses current application guides while preserving archival inventory."""
from backend.app.integrations.codex import scheme_evidence as evidence
from backend.app.project_evidence import _code_category
from backend.tests.integrations.test_scheme_evidence import repository, setup


def test_project_guides_are_read_without_changing_shared_evidence_policy(tmp_path):
    repo = repository(tmp_path)
    (repo / 'docs').mkdir()
    (repo / 'docs' / 'interaction_analysis.md').write_text(
        '# Interaction analysis\nCompare binding modes across ligand trajectories.\n')
    (repo / 'documentation').mkdir()
    (repo / 'documentation' / 'workflow.rst').write_text(
        'Workflow\n========\nPrepare systems, simulate, then compare molecular interactions.\n')
    (repo / 'application.py').write_text('def run(): return analyze()\n')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        sources = {source.get('path'): source for source in result['sources']}
        assert sources['docs/interaction_analysis.md']['provenance'] == 'project documentation'
        assert 'binding modes' in sources['docs/interaction_analysis.md']['text']
        assert 'documentation/workflow.rst' in sources
        assert _code_category('docs/interaction_analysis.md') is None
        assert evidence._scheme_category('uploads/private.md') is None
    finally:
        engine.dispose()


def test_archival_and_hidden_tools_do_not_displace_current_implementation(tmp_path):
    repo = repository(tmp_path)
    for directory in ('application', '_changed_20260723_132035', '.claude/hooks', 'archives'):
        (repo / directory).mkdir(parents=True)
        (repo / directory / 'analysis.py').write_text('def analyze(): return process()\n')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        sources = {source.get('path') for source in result['sources']}
        assert 'application/analysis.py' in sources
        for path in ('_changed_20260723_132035/analysis.py', '.claude/hooks/analysis.py', 'archives/analysis.py'):
            assert not evidence._production_path(path)
            assert path not in sources
            assert any(item['path'] == path for item in result['tree'])
        assert not evidence._shared_feature_stems([
            'application/analysis.py', '_changed_20260723_132035/analysis.py'])
    finally:
        engine.dispose()


def test_long_guide_keeps_exact_late_cli_purpose_and_outline():
    early_help = 'usage: simulate [-h]\n\nRun molecular dynamics for prepared structures.\n\noptions:\n'
    options = ''.join(f'  --option-{index}  detailed option documentation\n' for index in range(100))
    purpose = 'Compare binding modes across multiple ligand trajectories.'
    guide = ('# Interaction workflow\n\nGenerate and compare trajectory interactions.\n\n'
             '## Simulation\n\n```\n' + early_help + options + '```\n\n'
             '## Comparison\n\n```\nusage: compare [-h]\n\n' + purpose +
             '\n\noptions:\n' + options + '```\n\n## Outputs\n\nComparison reports.\n')
    excerpt, omitted = evidence._documentation_excerpt(guide)
    assert omitted
    assert purpose in excerpt
    assert 'Run molecular dynamics for prepared structures.' in excerpt
    assert '# Interaction workflow' in excerpt and '## Outputs' in excerpt
    assert len(excerpt) <= evidence._MAX_EXCERPT
    assert '--option-99' not in excerpt
    assert all(span in guide for span in excerpt.split(evidence.GAP))


def test_guide_outline_does_not_promote_code_comments_or_incomplete_fences():
    guide = '# Guide\n\nRead the workflow.\n\n```python\n# Not a document heading\n' + 'print("untrusted")\n' * 100
    excerpt, omitted = evidence._documentation_excerpt(guide)
    assert omitted
    assert '# Not a document heading' not in excerpt
    assert 'untrusted' not in excerpt
    assert 'Read the workflow.' in excerpt


def test_command_guides_survive_output_budget_before_peripheral_guides(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'docs').mkdir()
    (repo / 'application.py').write_text('def main(): return compare()\n')
    (repo / 'setup.py').write_text('from setuptools import setup\n'
        'setup(entry_points={"console_scripts": ["compare = application:main"]})\n')
    for index in range(8):
        (repo / 'docs' / f'aaa_{index}.md').write_text('# Supporting guide\n\n' + 'Peripheral information. ' * 30)
    (repo / 'docs' / 'zz_analysis.md').write_text(
        '# Analysis\n\nUse `compare` to compare observed binding modes.\n')
    monkeypatch.setattr(evidence, '_MAX_SOURCES_CHARS', 1200)
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        sources = {source.get('path'): source for source in result['sources']}
        assert 'application.py' in sources
        assert 'docs/zz_analysis.md' in sources
        assert 'binding modes' in sources['docs/zz_analysis.md']['text']
        assert 'source_output_budget' in result['coverage']['omitted_reason_counts']
    finally:
        engine.dispose()


def test_historical_manifests_do_not_exhaust_current_evidence_or_dependency_slots(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'old_envs').mkdir()
    (repo / 'application').mkdir()
    (repo / 'old_envs' / 'env.yml').write_text('dependencies:\n' + ''.join(
        f'  - archived-package-{index:03d}=1.0\n' for index in range(130)))
    (repo / 'application' / 'env.yml').write_text('dependencies:\n  - current-engine=2.0\n')
    (repo / 'application' / 'main.py').write_text('def run(): return prepare()\n')
    monkeypatch.setattr(evidence, '_MAX_SOURCES_CHARS', 1200)
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        assert 'current-engine' in {item['name'] for item in result['dependencies']}
        assert 'application/env.yml' in {source.get('path') for source in result['sources']}
        assert 'old_envs/env.yml' not in {source.get('path') for source in result['sources']}
        assert any(item['path'] == 'old_envs/env.yml' for item in result['tree'])
    finally:
        engine.dispose()
