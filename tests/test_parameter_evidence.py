"""Code/config parameter facts, static safety, provenance and bounded recall."""
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.integrations.codex import parameter_evidence as evidence
from backend.tests.integrations.test_scheme_evidence import repository, setup


def test_python_defaults_expressions_required_options_and_context_are_static(tmp_path):
    repo = repository(tmp_path)
    (repo / 'settings.py').write_text('''from dataclasses import dataclass
import argparse
import os
# Independent workers reduce elapsed time but increase concurrent memory use.
DEFAULT_EXTRACTION_WORKERS = 10
MAX_CONTEXT_CHARS = 40000
@dataclass
class Settings:
    port: int
    generation_budget: int = 900
    def modify(self):
        scratch: int = 12
def build_parser(timeout=600, *, batch_size=20):
    parser = argparse.ArgumentParser()
    parser.add_argument('--cutoff', default=8.0, help='Distance cutoff in angstroms.')
    parser.add_argument('--workers', default=os.cpu_count())
    parser.add_argument('--input', required=True)
    parser.add_argument('--optional')
    timeout = os.getenv('PROJECT_TIMEOUT', '600')
    if MAX_CONTEXT_CHARS < len(text):
        continue_processing()
raise RuntimeError('MUST_NOT_EXECUTE')
''')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_parameter_evidence(engine, 1)
        parameters = {item['name']: item for item in result['parameters']}
        assert parameters['DEFAULT_EXTRACTION_WORKERS']['default_value'] == '10'
        assert parameters['generation_budget']['default_value'] == '900'
        assert parameters['port']['default_kind'] == 'required'
        assert parameters['--workers']['default_kind'] == 'expression'
        assert parameters['--workers']['default_value'] == 'os.cpu_count()'
        assert parameters['--input']['default_kind'] == 'required'
        assert parameters['--optional']['default_kind'] == 'unspecified'
        assert parameters['timeout']['default_value'] == '600'
        assert parameters['batch_size']['default_value'] == '20'
        assert parameters['PROJECT_TIMEOUT']['default_value'] == "'600'"
        assert 'scratch' not in parameters
        sources = {source['id']: source for source in result['sources']}
        worker = sources[parameters['DEFAULT_EXTRACTION_WORKERS']['source_ids'][0]]
        assert 'increase concurrent memory' in worker['text']
        assert 'Distance cutoff in angstroms' in sources[parameters['--cutoff']['source_ids'][0]]['text']
        assert 'if MAX_CONTEXT_CHARS < len(text)' in sources[parameters['MAX_CONTEXT_CHARS']['source_ids'][0]]['text']
        assert worker['path'] == 'settings.py' and worker['line_start'] == 5
        assert len(worker['revision']) == 64
        assert 'MUST_NOT_EXECUTE' not in json.dumps(result)
        repeated = evidence.collect_parameter_evidence(engine, 1)
        assert repeated['fingerprint'] == result['fingerprint']
        (repo / 'settings.py').write_text('DEFAULT_EXTRACTION_WORKERS = 4\n')
        assert evidence.collect_parameter_evidence(engine, 1)['fingerprint'] != result['fingerprint']
    finally:
        engine.dispose()


def test_config_files_are_sanitized_and_duplicate_declarations_preserved(tmp_path):
    repo = repository(tmp_path)
    (repo / 'config.toml').write_text('[ai]\nworkers = 10\ncontext_budget = 40000\napi_key = "SECRET_TOML"\n')
    (repo / 'settings.json').write_text('{"limit":10,"limit":20,"auth":{"value":"SECRET_JSON"},"endpoint":"https:\\/\\/user:SECRET_URL@example.test"}')
    (repo / 'options.yaml').write_text('analysis:\n  cutoff: 8.0\n  context_budget: 16000\n  data_dir: /home/private/SECRET_PATH\n  password: SECRET_YAML\n')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_parameter_evidence(engine, 1)
        records = result['parameters']
        assert any(row['name'] == 'ai.workers' and row['default_value'] == '10' for row in records)
        assert {row['default_value'] for row in records if row['name'] == 'limit'} == {'10', '20'}
        assert any(row['name'] == 'analysis.cutoff' and row['default_value'] == '8.0' for row in records)
        assert 'SECRET_' not in json.dumps(result)
        assert len({row['id'] for row in records}) == len(records)
        for row in records:
            assert row['line_start'] > 0 and row['line_end'] >= row['line_start']
    finally:
        engine.dispose()


def test_secret_fields_values_comments_ignored_paths_and_symlinks_never_supply_evidence(tmp_path):
    repo = repository(tmp_path)
    (repo / 'config.py').write_text('''import os
DEFAULT_API_KEY = 'SECRET_KEY'
MAX_TOKENS = 2000
DEFAULT_TOKEN = 'SECRET_TOKEN'
DEFAULT_ENDPOINT = 'https://user:SECRET_URL@example.test'
DEFAULT_PATH = '/home/private/SECRET_PATH'
DEFAULT_SECRET_BUDGET = 12
MAX_WINDOW = os.getenv('API_KEY', 'SECRET_ALIAS')
# password=SECRET_COMMENT
MAX_BATCH = 20
''')
    (repo / '.env').write_text('SECRET_ENV')
    (repo / 'auth.json').write_text('{"workers": 999}')
    (repo / '.gitignore').write_text('ignored.py\n')
    (repo / 'ignored.py').write_text('MAX_WORKERS = 777\n')
    (repo / 'settings_link.py').symlink_to(repo / '.env')
    (repo / 'tests').mkdir()
    (repo / 'tests' / 'settings.py').write_text('DEFAULT_WORKERS = 444\n')
    (repo / 'archives').mkdir()
    (repo / 'archives' / 'settings.py').write_text('DEFAULT_WORKERS = 555\n')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_parameter_evidence(engine, 1)
        assert {row['name'] for row in result['parameters']} == {'MAX_TOKENS', 'MAX_BATCH'}
        assert 'SECRET_' not in json.dumps(result)
        assert all(source['path'] == 'config.py' for source in result['sources'])
    finally:
        engine.dispose()


def test_explicit_config_exclusion_is_preserved(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'config.toml').write_text('limit = 10\n')
    monkeypatch.setenv('PROJECTHUB_EVIDENCE_EXCLUDE', '["config.toml"]')
    engine = setup(tmp_path, repo)
    try:
        assert evidence.collect_parameter_evidence(engine, 1)['parameters'] == []
    finally:
        engine.dispose()


def test_parameters_and_evidence_remain_atomic_under_limits_and_no_repository_is_safe(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('\n'.join(f'MAX_LIMIT_{index} = {index}' for index in range(140)))
    engine = setup(tmp_path, repo)
    try:
        monkeypatch.setattr(evidence, 'MAX_PARAMETERS', 7)
        result = evidence.collect_parameter_evidence(engine, 1)
        assert len(result['parameters']) == len(result['sources']) == 7
        assert result['coverage']['parameters_omitted'] == 133
        source_ids = {source['id'] for source in result['sources']}
        assert all(set(row['source_ids']) <= source_ids for row in result['parameters'])
        assert result['coverage']['partial']
        foreign = evidence.collect_parameter_evidence(engine, 2)
        assert foreign['parameters'] == [] and foreign['sources'] == []
        assert foreign['coverage']['partial']
        with pytest.raises(HTTPException) as error:
            evidence.collect_parameter_evidence(engine, 999)
        assert error.value.status_code == 404
    finally:
        engine.dispose()


def test_unsupported_yaml_subtrees_are_not_reinterpreted_as_unconditional_settings(tmp_path):
    repo = repository(tmp_path)
    (repo / 'settings.yaml').write_text('defaults: &base\n  workers: 100\nanalysis:\n  cutoff: 8\n')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_parameter_evidence(engine, 1)
        assert [row['name'] for row in result['parameters']] == ['analysis.cutoff']
        assert result['coverage']['partial']
    finally:
        engine.dispose()


def test_same_line_conflicting_python_and_json_declarations_have_distinct_ids(tmp_path):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('MAX_SIZE=1; MAX_SIZE=2\n')
    (repo / 'settings.json').write_text('{"timeout":1,"timeout":2}')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_parameter_evidence(engine, 1)
        assert len(result['parameters']) == 4
        assert len({row['id'] for row in result['parameters']}) == 4
        assert {row['default_value'] for row in result['parameters']} == {'1', '2'}
    finally:
        engine.dispose()


def test_toml_reopened_tables_keep_actual_scope_value_and_line(tmp_path):
    repo = repository(tmp_path)
    (repo / 'config.toml').write_text('[a]\ntimeout=10\n[b]\ntimeout=20\n[a.child]\ntimeout=30\n')
    engine = setup(tmp_path, repo)
    try:
        rows = {row['name']: row for row in evidence.collect_parameter_evidence(engine, 1)['parameters']}
        assert (rows['a.timeout']['default_value'], rows['a.timeout']['line_start']) == ('10', 2)
        assert (rows['b.timeout']['default_value'], rows['b.timeout']['line_start']) == ('20', 4)
        assert (rows['a.child.timeout']['default_value'], rows['a.child.timeout']['line_start']) == ('30', 6)
        assert all(row['declaration_kind'] == 'configuration_value' for row in rows.values())
    finally:
        engine.dispose()


def test_json_spans_do_not_substitute_array_examples_or_escaped_keys(tmp_path):
    repo = repository(tmp_path)
    (repo / 'settings.json').write_text('{"examples":[{"timeout":999}],"\\u0074imeout":10,"timeout":20}')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_parameter_evidence(engine, 1)
        assert [row['default_value'] for row in result['parameters']] == ['10', '20']
        assert result['coverage']['partial']
    finally:
        engine.dispose()


def test_invalid_yaml_quoted_scalar_is_omitted(tmp_path):
    repo = repository(tmp_path)
    (repo / 'settings.yaml').write_text("timeout: 'unterminated\nworkers: 4\n")
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_parameter_evidence(engine, 1)
        assert [row['name'] for row in result['parameters']] == ['workers']
        assert result['coverage']['partial']
    finally:
        engine.dispose()


def test_authorization_basic_and_token_comments_are_not_transmitted(tmp_path):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text("# token=SECRET_COMMENT\nMAX_BUDGET=10\nDEFAULT_MODE='Basic SECRET_BASE64'\nMAX_TOKENS=4000\n")
    (repo / 'config.json').write_text('{"authorization_header":"SECRET_AUTH","token_budget":500}')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_parameter_evidence(engine, 1)
        assert 'SECRET_' not in json.dumps(result)
        assert {row['name'] for row in result['parameters']} == {'MAX_BUDGET', 'MAX_TOKENS', 'token_budget'}
    finally:
        engine.dispose()


def test_sensitive_guard_lookups_do_not_leak_values_or_erase_conditionality(tmp_path):
    repo = repository(tmp_path)
    (repo / 'config.py').write_text("MAX_LIMIT=10\nif config.get('api_key', 'SECRET_CONDITION'):\n    MAX_LIMIT=2\nif MAX_LIMIT == config.get('token', 'SECRET_GUARD'):\n    reject()\n")
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_parameter_evidence(engine, 1)
        assert len(result['parameters']) == 2
        assert 'SECRET_' not in json.dumps(result)
        assert result['parameters'][1]['condition'] == 'if [condition omitted for safety]'
    finally:
        engine.dispose()


def test_sensitive_subscript_lookups_do_not_leak_defaults_or_guard_values(tmp_path):
    repo = repository(tmp_path)
    (repo / 'config.py').write_text("DEFAULT_LIMIT=config['access_token']\nif config['api_key']=='SECRET_GUARD':\n    MAX_WORKERS=10\n")
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_parameter_evidence(engine, 1)
        assert [row['name'] for row in result['parameters']] == ['MAX_WORKERS']
        assert result['parameters'][0]['condition'] == 'if [condition omitted for safety]'
        assert 'SECRET_GUARD' not in json.dumps(result)
        assert 'access_token' not in json.dumps(result)
    finally:
        engine.dispose()


def test_sensitive_attribute_accessors_do_not_leak_defaults_or_conditions(tmp_path):
    repo = repository(tmp_path)
    (repo / 'config.py').write_text("DEFAULT_LIMIT=getattr(config, 'api_key', 'SECRET_FALLBACK')\nif getattr(config, 'access_token', 'SECRET_CONDITION'):\n    MAX_WORKERS=10\nif hasattr(config, 'api_key') and marker == 'SECRET_GUARD':\n    MAX_WORKERS=2\n")
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_parameter_evidence(engine, 1)
        assert [row['name'] for row in result['parameters']] == ['MAX_WORKERS', 'MAX_WORKERS']
        assert all(row['condition'] == 'if [condition omitted for safety]' for row in result['parameters'])
        assert 'SECRET_' not in json.dumps(result)
    finally:
        engine.dispose()


def test_exception_and_match_defaults_preserve_branch_conditions_without_evaluation():
    rows = evidence._python('''try:
    MAX_WORKERS = discover_workers()
except RuntimeError:
    MAX_WORKERS = 2
else:
    MAX_WORKERS = 4
finally:
    MAX_BUDGET = 500
match platform:
    case 'windows':
        DEFAULT_THREADS = 2
    case _ if getattr(config, 'api_key', 'SECRET_MATCH'):
        DEFAULT_THREADS = 10
''')
    assert [row['condition'] for row in rows] == [
        'try branch', 'except branch: RuntimeError', 'else branch of try', 'finally branch of try',
        "match platform: case 'windows'", 'match platform: case _ if [condition omitted for safety]',
    ]
    assert 'SECRET_MATCH' not in json.dumps(rows)
    assert [row['default_value'] for row in rows] == ['discover_workers()', '2', '4', '500', '2', '10']


def test_loop_and_context_managed_defaults_are_not_presented_as_unconditional():
    rows = evidence._python('''for size in configured_sizes:
    MAX_BATCH = size
else:
    MAX_BATCH = 10
while supported():
    DEFAULT_THREADS = 4
else:
    DEFAULT_THREADS = 2
with optional_backend():
    MAX_BUDGET = 200
''')
    assert [row['condition'] for row in rows] == [
        'for size in configured_sizes', 'else branch of for', 'while supported()',
        'else branch of while supported()', 'with optional_backend()',
    ]


def test_sensitive_match_patterns_and_comment_continuations_are_not_transmitted():
    rows = evidence._python('''# password=
# Continued secret material:
# SECRET_CONTINUATION
MAX_WORKERS=4
match config:
    case {'api_key': 'SECRET_MAPPING'}:
        MAX_WORKERS=2
    case Credentials(access_token='SECRET_CLASS'):
        MAX_WORKERS=1
''')
    assert rows[0]['context'] == ''
    assert [row['condition'] for row in rows[1:]] == [
        'match config: case [condition omitted for safety]',
        'match config: case [condition omitted for safety]',
    ]
    assert 'SECRET_' not in json.dumps(rows)


@pytest.mark.parametrize(('filename', 'body', 'first', 'second'), [
    ('settings.py', '# Bound selected context.\nMAX_CONTEXT_CHARS=40_000\nDEFAULT_WORKERS=10\n', 'MAX_CONTEXT_CHARS', 'DEFAULT_WORKERS'),
    ('config.toml', '# Bound selected context.\ncontext_budget=40000\nworkers=10\n', 'context_budget', 'workers'),
    ('settings.yaml', '# Bound selected context.\ncontext_budget: 40000\nworkers: 10\n', 'context_budget', 'workers'),
])
def test_parameter_comments_never_cross_an_intervening_declaration(tmp_path, filename, body, first, second):
    repo = repository(tmp_path)
    (repo / filename).write_text(body)
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_parameter_evidence(engine, 1)
        rows = {row['name']: row for row in result['parameters']}
        sources = {source['id']: source for source in result['sources']}
        assert 'Bound selected context.' in sources[rows[first]['source_ids'][0]]['text']
        assert 'Bound selected context.' not in sources[rows[second]['source_ids'][0]]['text']
    finally:
        engine.dispose()


def test_comment_context_is_only_the_immediately_adjacent_whole_block():
    lines = ['# Earlier explanation.', '', '# First adjacent line.', '# Second adjacent line.', 'MAX_LIMIT=10']
    assert evidence._comments(lines, 5) == '# First adjacent line.\n# Second adjacent line.'
    assert evidence._comments(['# Detached explanation.', '', 'MAX_LIMIT=10'], 3) == ''


def test_multiline_public_function_default_range_contains_the_value():
    rows = evidence._python('def process(timeout =\n    600):\n    return timeout\n')
    assert len(rows) == 1
    assert rows[0]['default_value'] == '600'
    assert rows[0]['line_start'] == 1 and rows[0]['line_end'] == 2


def test_related_guards_stay_in_lexical_scope_and_conditional_defaults_remain_qualified():
    source = '''MAX_WORKERS=10
if platform_is_windows():
    MAX_WORKERS=2
else:
    MAX_WORKERS=8
def request(timeout=1):
    if timeout < 5:
        raise ValueError()
def simulate(timeout=20):
    if timeout > 30:
        return None
def shadow(MAX_WORKERS=3):
    if MAX_WORKERS == 3:
        return None
'''
    rows = evidence._python(source)
    request = next(row for row in rows if row['symbol'] == 'request')
    simulation = next(row for row in rows if row['symbol'] == 'simulate')
    assert 'timeout < 5' in request['context'] and 'timeout > 30' not in request['context']
    assert 'timeout > 30' in simulation['context'] and 'timeout < 5' not in simulation['context']
    constants = [row for row in rows if row['symbol'] == 'MAX_WORKERS']
    assert constants[0]['condition'] == ''
    assert constants[1]['condition'] == 'if platform_is_windows()'
    assert constants[2]['condition'] == 'else branch of if platform_is_windows()'
    assert all('MAX_WORKERS == 3' not in row['context'] for row in constants)


def test_large_python_prefix_keeps_complete_leading_defaults_without_invented_closures(tmp_path):
    repo = repository(tmp_path)
    (repo / 'settings.py').write_text('MAX_CONTEXT_CHARS=40000\ndef huge_function():\n' + '    # padding\n' * 7000 + '    MAX_WORKERS=10\n')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_parameter_evidence(engine, 1)
        assert [row['name'] for row in result['parameters']] == ['MAX_CONTEXT_CHARS']
        assert result['sources'][0]['partial'] is True
        assert result['coverage']['partial'] and not result['coverage']['freshness_unknown']
    finally:
        engine.dispose()


@pytest.mark.parametrize('packaging', ['setup.py', 'pyproject.toml'])
def test_declared_cli_scripts_are_prioritized_without_execution(tmp_path, monkeypatch, packaging):
    repo = repository(tmp_path)
    (repo / 'scripts').mkdir()
    (repo / 'scripts' / 'runner.py').write_text("def main():\n    parser.add_argument('--cutoff', default=8)\n")
    (repo / 'main.py').write_text('MAX_CONTEXT_CHARS=40000\n')
    declaration = ('from setuptools import setup\nraise RuntimeError("MUST_NOT_EXECUTE")\n'
        'setup(entry_points={"console_scripts":["run = scripts.runner:main"]})\n'
        if packaging == 'setup.py' else '[project.scripts]\nrun="scripts.runner:main"\n')
    (repo / packaging).write_text(declaration)
    monkeypatch.setattr(evidence, 'MAX_PARAMETERS', 1)
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_parameter_evidence(engine, 1)
        assert [row['name'] for row in result['parameters']] == ['--cutoff']
        assert 'MUST_NOT_EXECUTE' not in json.dumps(result)
    finally:
        engine.dispose()


def test_other_language_coverage_is_not_claimed_complete(tmp_path):
    repo = repository(tmp_path)
    (repo / 'main.ts').write_text('const MAX_CONTEXT=999;')
    (repo / 'settings.json').write_text('{"workers":4}')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_parameter_evidence(engine, 1)
        assert len(result['parameters']) == 1
        assert result['coverage']['unsupported_language_files'] == 1
        assert result['coverage']['partial']
        assert result['coverage']['supported_formats']
    finally:
        engine.dispose()
