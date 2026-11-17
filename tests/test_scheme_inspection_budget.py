"""Read-only Scheme inspection budgets; real local Git/files, no App Server or AI."""
import json
import os
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.integrations.codex import scheme_evidence as evidence
from backend.app.models import ProjectHistoryBatch
from backend.tests.integrations.test_scheme_evidence import repository, setup


def source_paths(result):
    return {source['path'] for source in result['sources'] if source.get('path')}


def test_inspects_more_than_24_modules_and_beyond_tree_display_cap(tmp_path):
    repo = repository(tmp_path)
    for index in range(305):
        (repo / f'module_{index:03}.py').write_text(f'class Feature{index}: pass\n')
    engine = setup(tmp_path, repo)
    try:
        first = evidence.collect_evidence(engine, 1)
        assert first['coverage']['files_inspected'] == 305
        assert len(first['tree']) == 300
        assert 'module_304.py' in source_paths(first)
        assert first['diagnostics']['bytes_read'] == sum(path.stat().st_size for path in repo.glob('*.py'))
        (repo / 'module_304.py').write_text('class ChangedFeature: pass\n')
        changed = evidence.collect_evidence(engine, 1)
        assert changed['diagnostics']['cache_hits'] == 0
        assert changed['fingerprint'] != first['fingerprint']
    finally:
        engine.dispose()


def test_read_budget_charges_binary_oversize_and_invalid_utf8_raw_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, '_MAX_BYTES', 32)
    contents = {'binary.py': b'\0' * 20, 'requirements.txt': b'x' * 100,
                'invalid.py': b'\xff' * 10, 'unicode.py': '# éééé\n'.encode()}
    for relative, data in contents.items():
        (tmp_path / relative).write_bytes(data)
    root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    budget = evidence._InspectionBudget()
    try:
        for relative in ('binary.py', 'requirements.txt', 'invalid.py'):
            with pytest.raises(ValueError):
                evidence._read(root_fd, relative, budget=budget)
        assert evidence._read(root_fd, 'unicode.py', budget=budget) == '# éééé\n'
        assert budget.bytes_read == 20 + 33 + 10 + len(contents['unicode.py'])
    finally:
        os.close(root_fd)


def test_byte_budget_stops_inside_read_without_partial_manifest(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'requirements.txt').write_text('fastapi>=0.115\n' * 30)
    monkeypatch.setattr(evidence, '_MAX_INSPECTION_BYTES', 20)
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        assert result['diagnostics']['bytes_read'] == 20
        assert result['coverage']['inspection_stopped'] == 'byte_budget'
        assert result['coverage']['omitted_reason_counts']['byte_budget'] == 1
        assert not result['dependencies']
        assert 'requirements.txt' not in source_paths(result)
    finally:
        engine.dispose()


def test_default_byte_budget_bounds_rejected_binary_inputs(tmp_path):
    repo = repository(tmp_path)
    for index in range(40):
        (repo / f'module_{index:03}.py').write_bytes(b'\0' * 70_000)
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        assert result['diagnostics']['bytes_read'] == evidence._MAX_INSPECTION_BYTES
        assert result['coverage']['inspection_stopped'] == 'byte_budget'
        assert result['coverage']['files_inspected'] == 0
        assert result['diagnostics']['files_attempted'] > 24
        assert result['coverage']['omitted_reason_counts']['unreadable_or_malformed'] > 24
    finally:
        engine.dispose()


def test_time_budget_partial_is_not_cached(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('class Main: pass\n')
    (repo / 'other.py').write_text('class Other: pass\n')
    clock = SimpleNamespace(value=time.monotonic())
    monkeypatch.setattr(evidence, 'time', SimpleNamespace(monotonic=lambda: clock.value))
    original_read = evidence._read

    def slow_read(*args, **kwargs):
        result = original_read(*args, **kwargs)
        clock.value += evidence._INSPECTION_SECONDS
        return result

    monkeypatch.setattr(evidence, '_read', slow_read)
    engine = setup(tmp_path, repo)
    try:
        partial = evidence.collect_evidence(engine, 1)
        assert partial['coverage']['inspection_stopped'] == 'time_budget'
        assert partial['coverage']['files_inspected'] == 1
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(ProjectHistoryBatch)) == 0
        monkeypatch.setattr(evidence, '_read', original_read)
        complete = evidence.collect_evidence(engine, 1)
        assert complete['diagnostics']['cache_hits'] == 0
        assert complete['coverage']['files_inspected'] == 2
        assert complete['coverage']['inspection_stopped'] is None
        assert evidence.collect_evidence(engine, 1)['diagnostics']['cache_hits'] == 1
    finally:
        engine.dispose()


def test_time_budget_in_final_parse_is_not_cached(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('class Main: pass\n')
    clock = SimpleNamespace(value=time.monotonic())
    monkeypatch.setattr(evidence, 'time', SimpleNamespace(monotonic=lambda: clock.value))
    original = evidence._structural_excerpt

    def slow_excerpt(*args):
        clock.value += evidence._INSPECTION_SECONDS
        return original(*args)

    monkeypatch.setattr(evidence, '_structural_excerpt', slow_excerpt)
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        assert result['coverage']['inspection_stopped'] == 'time_budget'
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(ProjectHistoryBatch)) == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize('budget_name', ['_MAX_INSPECTION_BYTES', '_INSPECTION_SECONDS', '_MAX_METADATA_BYTES', '_MAX_PACKAGE_CHARS'])
def test_budget_configuration_invalidates_cache(tmp_path, monkeypatch, budget_name):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('class Main: pass\n')
    engine = setup(tmp_path, repo)
    try:
        evidence.collect_evidence(engine, 1)
        assert evidence.collect_evidence(engine, 1)['diagnostics']['cache_hits'] == 1
        monkeypatch.setattr(evidence, budget_name, getattr(evidence, budget_name) * 2)
        assert evidence.collect_evidence(engine, 1)['diagnostics']['cache_hits'] == 0
    finally:
        engine.dispose()


def test_output_budget_is_distinct_from_inspection_budget(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    for index in range(30):
        (repo / f'feature_{index:02}.py').write_text('class Feature: pass\n' * 3)
    monkeypatch.setattr(evidence, '_MAX_SOURCES_CHARS', 400)
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        assert result['coverage']['files_inspected'] == 30
        assert result['coverage']['inspection_stopped'] is None
        assert result['coverage']['omitted_reason_counts']['source_output_budget'] > 0
        assert result['diagnostics']['input_chars'] <= 400
    finally:
        engine.dispose()


def test_declared_entrypoint_is_read_before_small_budget_expires(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    declaration = '[project.scripts]\nlaunch="zzz:main"\n'
    (repo / 'pyproject.toml').write_text(declaration)
    (repo / 'zzz.py').write_text('def main(): pass\n')
    for index in range(30):
        (repo / f'aaa_{index:02}.py').write_text('class Other: pass\n')
    monkeypatch.setattr(evidence, '_MAX_INSPECTION_BYTES', len(declaration.encode()) + 30)
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        assert 'zzz.py' in source_paths(result)
        assert result['coverage']['inspection_stopped'] == 'byte_budget'
    finally:
        engine.dispose()


def test_metadata_budget_partial_is_not_cached(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('class Main: pass\n')
    (repo / 'other.py').write_text('class Other: pass\n')
    monkeypatch.setattr(evidence, '_MAX_METADATA_BYTES', 270)
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        assert result['coverage']['files_inspected'] == 1
        assert any('metadata byte budget' in limit for limit in result['coverage']['limitations'])
        assert evidence.collect_evidence(engine, 1)['diagnostics']['cache_hits'] == 0
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(ProjectHistoryBatch)) == 0
    finally:
        engine.dispose()


def test_deadline_checked_after_each_raw_read_and_charges_returned_bytes(tmp_path, monkeypatch):
    (tmp_path / 'main.py').write_bytes(b'x' * 20_000)
    clock = SimpleNamespace(value=time.monotonic())
    monkeypatch.setattr(evidence, 'time', SimpleNamespace(monotonic=lambda: clock.value))
    raw_read = os.read
    calls = []

    def slow_read(fd, maximum):
        chunk = raw_read(fd, maximum)
        calls.append(len(chunk))
        clock.value += evidence._INSPECTION_SECONDS
        return chunk

    monkeypatch.setattr(evidence.os, 'read', slow_read)
    root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    budget = evidence._InspectionBudget()
    try:
        with pytest.raises(evidence._InspectionStopped) as stopped:
            evidence._read(root_fd, 'main.py', budget=budget)
        assert stopped.value.reason == 'time_budget'
        assert calls == [8192]
        assert budget.bytes_read == 8192
    finally:
        os.close(root_fd)


def test_metadata_deadline_partial_is_not_cached(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('class Main: pass\n')
    (repo / 'other.py').write_text('class Other: pass\n')
    clock = SimpleNamespace(value=time.monotonic())
    monkeypatch.setattr(evidence, 'time', SimpleNamespace(monotonic=lambda: clock.value))
    original = evidence._descriptor
    first_open = True

    def delayed_descriptor(*args):
        nonlocal first_open
        fd = original(*args)
        if first_open:
            first_open = False
            clock.value += evidence._METADATA_SECONDS
        return fd

    monkeypatch.setattr(evidence, '_descriptor', delayed_descriptor)
    engine = setup(tmp_path, repo)
    try:
        partial = evidence.collect_evidence(engine, 1)
        assert partial['coverage']['files_inspected'] == 1
        assert any('metadata time budget' in limit for limit in partial['coverage']['limitations'])
        assert partial['coverage']['file_omissions'] == {
            'total': 1, 'files': [{'path': 'other.py', 'reason': 'metadata_time_budget'}],
        }
        monkeypatch.setattr(evidence, '_descriptor', original)
        complete = evidence.collect_evidence(engine, 1)
        assert complete['diagnostics']['cache_hits'] == 0
        assert complete['coverage']['files_inspected'] == 2
    finally:
        engine.dispose()


@pytest.mark.parametrize('category', ['docs', 'manifests'])
def test_large_document_or_manifest_sets_do_not_starve_application_code(tmp_path, category):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('class Application: pass\n')
    for index in range(40):
        area = repo / f'area_{index:02}'
        area.mkdir()
        if category == 'docs':
            (area / 'README.md').write_text('# Features\n\n' + 'Useful capability.\n' * 3200)
        else:
            (area / 'package.json').write_text('{"description":"' + 'x' * 60_000 + '"}')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        assert 'main.py' in source_paths(result)
        assert result['coverage']['inspection_stopped'] == 'byte_budget'
        assert result['diagnostics']['bytes_read'] == evidence._MAX_INSPECTION_BYTES
    finally:
        engine.dispose()


def test_serialized_metadata_and_real_generation_prompt_fit_with_many_tiny_long_paths(tmp_path):
    from backend.app.integrations.codex import scheme

    repo = repository(tmp_path)
    (repo / 'pyproject.toml').write_text('[project.scripts]\nlaunch="zzz:main"\n')
    (repo / 'zzz.py').write_text('def main(): pass\n')
    area = repo / ('features_' + 'x' * 80)
    area.mkdir()
    for index in range(450):
        (area / (f'feature_{index:03}_' + 'y' * 60 + '.py')).write_text(f'class Feature{index}: pass\n')
    engine = setup(tmp_path, repo)
    try:
        result = evidence.collect_evidence(engine, 1)
        assert result['coverage']['files_inspected'] == 452
        assert result['coverage']['inspection_stopped'] is None
        payload = {key: result[key] for key in ('sources', 'dependencies', 'tree', 'coverage', 'knowledge_context')}
        payload['coverage'] = {key: value for key, value in payload['coverage'].items() if key != 'file_omissions'}
        assert len(json.dumps(payload, ensure_ascii=False)) <= evidence._MAX_PACKAGE_CHARS
        assert result['diagnostics']['package_chars'] == len(json.dumps(payload, ensure_ascii=False))
        assert result['coverage']['omitted_reason_counts']['package_output_budget'] > 0
        assert 'zzz.py' in source_paths(result)
        allowed = {source['id'] for source in result['sources']}
        assert all(set(dependency['source_ids']) <= allowed for dependency in result['dependencies'])

        class Manager:
            prompt_chars = 0

            def generate_brief(self, prompt, schema):
                self.prompt_chars = len(prompt)
                assert '"file_omissions"' not in prompt
                graph = {'nodes': [], 'edges': [], 'explanation': 'Insufficient graph evidence.'}
                return json.dumps({'architecture': graph, 'workflow': graph, 'dependencies': []})

        manager = Manager()
        snapshot = scheme.refresh_scheme(engine, 1, manager)
        assert 0 < manager.prompt_chars <= scheme.MAX_INPUT_CHARS
        assert snapshot['coverage']['files_inspected'] == 452
        assert snapshot['coverage']['file_omissions'] == result['coverage']['file_omissions']
    finally:
        engine.dispose()


def test_package_trimming_preserves_whole_priority_source_and_dependency_references(monkeypatch):
    monkeypatch.setattr(evidence, '_MAX_PACKAGE_CHARS', 2000)
    priority = evidence._source('priority', 'Entrypoint', 'class Main: pass', 'observed in code')
    low_priority = evidence._source('later', 'Later helper', 'x' * 5000, 'observed in code')
    result = {'sources': [priority, low_priority],
              'dependencies': [{'id': 'package', 'source_ids': ['later']}], 'tree': [],
              'coverage': {'partial': False, 'limitations': [], 'omitted_reason_counts': {},
                           'conversation_excerpts': 0}}
    assert evidence._bound_package(result) <= evidence._MAX_PACKAGE_CHARS
    assert result['sources'] == [priority]
    assert result['sources'][0]['text'] == 'class Main: pass'
    assert result['dependencies'] == []
    assert result['coverage']['omitted_reason_counts']['package_output_budget'] == 2
