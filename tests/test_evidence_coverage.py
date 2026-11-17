"""Real temporary Git inventories and saved-diagnostic contracts; no AI calls."""
from copy import deepcopy
import hashlib
import json
import os
import time

import pytest

from backend.app import project_evidence as evidence
from backend.app.integrations.codex.evidence_coverage import knowledge_coverage, repository_coverage
from backend.app.integrations.codex.knowledge_brief import _repository_signature
from backend.tests.test_project_evidence import git, repository


def test_tracked_file_inventory_separates_full_partial_excluded_and_failed(tmp_path):
    repo = repository(tmp_path)
    (repo / 'long.py').write_text('def sample():\n' + '    value = 1\n' * 25)
    (repo / 'empty.py').write_text('')
    (repo / 'binary.py').write_bytes(b'\x00BINARY_SENTINEL')
    outside = tmp_path / 'outside.py'
    outside.write_text('EXTERNAL_SENTINEL')
    (repo / 'linked.py').symlink_to(outside)
    (repo / 'private.secret.py').write_text('PRIVATE_SENTINEL')
    (repo / '.env').write_text('ENV_SENTINEL')
    (repo / 'asset.png').write_bytes(b'\x00PNG_SENTINEL')
    git(repo, 'add', '.')
    (repo / 'untracked.py').write_text('UNTRACKED_SENTINEL')
    result = evidence.collect_repository_code(str(repo))
    measured = result['inspection']
    assert measured['inventory_complete'] is True
    assert measured['total_files'] == 11
    assert measured['eligible_files'] == 8
    assert measured['read_files'] == measured['ai_input_files'] == 5
    assert measured['full_files'] == 4 and measured['partial_files'] == 1
    assert measured['excluded_files'] == 2 and measured['unsupported_files'] == 1
    assert measured['failed_files'] == 2 and measured['empty_files'] == 1
    assert measured['not_selected_files'] == 0
    assert measured['paths']['partial'] == ['long.py']
    assert set(measured['paths']['failed']) == {'binary.py', 'linked.py'}
    assert measured['by_category']['code'] == {
        'eligible_files': 5, 'read_files': 2, 'full_files': 1, 'partial_files': 1}
    rendered = json.dumps(result)
    assert not any(value in rendered for value in ('private.secret.py', '.env', 'PRIVATE_SENTINEL',
                                                   'ENV_SENTINEL', 'EXTERNAL_SENTINEL',
                                                   'UNTRACKED_SENTINEL', 'BINARY_SENTINEL'))


def test_unselected_paths_bounded_without_losing_exact_denominator(tmp_path):
    repo = repository(tmp_path)
    for number in range(140):
        (repo / f'module{number:03}.py').write_text('value = 1\n')
    git(repo, 'add', '.')
    measured = evidence.collect_repository_code(str(repo))['inspection']
    assert measured['total_files'] == measured['eligible_files'] == 144
    assert measured['read_files'] == 10
    assert measured['not_selected_files'] == 134
    assert len(measured['paths']['not_selected']) == 100
    assert measured['paths_truncated'] is True


def test_missing_inventory_never_claims_zero_total_or_complete_coverage(tmp_path, monkeypatch):
    for result, status in ((evidence.collect_repository_code(None), 'not_linked'),
                           (evidence.collect_repository_code(str(tmp_path / 'missing')), 'unavailable')):
        measured = result['inspection']
        assert measured['status'] == status
        assert measured['total_files'] is None and measured['eligible_files'] is None
        assert measured['inventory_complete'] is False
    repo = repository(tmp_path)
    def timeout(*args, **kwargs):
        raise evidence.EvidenceReadError('Repository code inspection timed out.')
    monkeypatch.setattr(evidence, '_code_excerpt', timeout)
    result = evidence.collect_repository_code(str(repo))
    assert result['sources'] == []
    assert result['inspection']['status'] == 'unavailable'
    assert result['inspection']['ai_input_files'] == 0
    assert result['inspection']['total_files'] is None


def test_long_source_lines_are_partial_even_when_all_lines_are_selected(tmp_path):
    repo = repository(tmp_path)
    (repo / 'source.py').write_text('long_value = ' + repr('x' * 400) + '\n')
    git(repo, 'add', 'source.py')
    measured = evidence.collect_repository_code(str(repo))['inspection']
    assert 'source.py' in measured['paths']['partial']
    detail = next(item for item in measured['file_details'] if item['path'] == 'source.py')
    assert detail['local_read_complete'] is True and detail['excerpt_complete'] is False
    assert detail['omission_reasons'] == ['line_width_limit']


def test_per_file_details_distinguish_full_local_read_from_selected_declarations(tmp_path):
    repo = repository(tmp_path)
    body = 'def sample():\n' + '    value = 1\n' * 25
    (repo / 'long.py').write_text(body)
    git(repo, 'add', 'long.py')
    result = evidence.collect_repository_code(str(repo))
    measured = next(item for item in result['inspection']['file_details'] if item['path'] == 'long.py')
    original = next(source for source in result['sources'] if source['path'] == 'long.py')
    assert measured['file_bytes'] == measured['bytes_read'] == len(body.encode())
    assert measured['local_read_complete'] is True
    assert measured['excerpt_complete'] is False
    assert measured['omission_reasons'] == ['declaration_selection']
    assert measured['excerpt_chars'] == len(original['text'].split('\n', 2)[2])
    assert 'file_bytes' not in original and 'bytes_read' not in original


def test_large_source_records_exact_bounded_local_prefix_and_file_size(tmp_path):
    repo = repository(tmp_path)
    body = 'value = 1\n' * 8000
    (repo / 'large.py').write_text(body)
    git(repo, 'add', 'large.py')
    measured = next(item for item in evidence.collect_repository_code(str(repo))['inspection']['file_details']
                    if item['path'] == 'large.py')
    assert measured['file_bytes'] == len(body.encode())
    assert measured['bytes_read'] == 65536 < measured['file_bytes']
    assert measured['local_read_complete'] is False and measured['excerpt_complete'] is False
    assert 'byte_limit' in measured['omission_reasons']
    assert 'declaration_selection' in measured['omission_reasons']


def test_unicode_counts_bytes_and_excerpt_characters_independently(tmp_path):
    repo = repository(tmp_path)
    body = 'Léxico 🧪\n' * 3
    (repo / 'README.md').write_text(body)
    git(repo, 'add', 'README.md')
    measured = next(item for item in evidence.collect_repository_code(str(repo))['inspection']['file_details']
                    if item['path'] == 'README.md')
    assert measured['file_bytes'] == measured['bytes_read'] == len(body.encode('utf-8'))
    assert measured['excerpt_chars'] == len(body) < measured['file_bytes']
    assert measured['local_read_complete'] is True and measured['excerpt_complete'] is True
    assert measured['omission_reasons'] == []


def test_character_and_structural_excerpt_limits_report_different_reasons(tmp_path):
    repo = repository(tmp_path)
    (repo / 'README.md').write_text('Readable documentation. ' * 180)
    (repo / 'declarations.py').write_text('def same(): pass\n' * 200)
    git(repo, 'add', '.')
    details = {item['path']: item for item in evidence.collect_repository_code(str(repo))['inspection']['file_details']}
    for path, reason in (('README.md', 'character_limit'), ('declarations.py', 'excerpt_limit')):
        measured = details[path]
        assert measured['file_bytes'] == measured['bytes_read']
        assert measured['local_read_complete'] is True and measured['excerpt_complete'] is False
        assert measured['omission_reasons'] == [reason]


def test_multibyte_prefix_boundary_preserves_exact_local_byte_measurements(tmp_path):
    body = 'a' + '🧪' * 3
    (tmp_path / 'README.md').write_text(body)
    root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    measured = {}
    try:
        excerpt, partial = evidence._code_excerpt(root_fd, 'README.md', 2,
                                                  time.monotonic() + 5, measurements=measured)
    finally:
        os.close(root_fd)
    assert excerpt == 'a🧪' and partial is True
    assert measured['file_bytes'] == 13 and measured['bytes_read'] == 8
    assert measured['excerpt_chars'] == 2
    assert measured['local_read_complete'] is False and measured['excerpt_complete'] is False
    assert measured['omission_reasons'] == ['byte_limit', 'utf8_boundary']


@pytest.mark.parametrize('replacement', [b'extended contents\n', b'x', b'NEW\n'])
def test_files_changed_during_read_are_failed_not_complete_excerpts(tmp_path, monkeypatch, replacement):
    repo = repository(tmp_path)
    target = repo / 'changing.py'
    target.write_bytes(b'old\n')
    git(repo, 'add', 'changing.py')
    initial = target.stat()
    original_read = os.read
    changed = False

    def mutate_after_read(fd, maximum):
        nonlocal changed
        data = original_read(fd, maximum)
        opened = os.fstat(fd)
        if not changed and opened.st_dev == initial.st_dev and opened.st_ino == initial.st_ino:
            changed = True
            target.write_bytes(replacement)
            # Force a distinct timestamp even on a filesystem with coarser
            # timestamp resolution, including the same-size replacement case.
            os.utime(target, ns=(initial.st_atime_ns, initial.st_mtime_ns + 1_000_000_000))
        return data

    monkeypatch.setattr(evidence.os, 'read', mutate_after_read)
    result = evidence.collect_repository_code(str(repo))
    measured = result['inspection']
    assert changed
    assert measured['status'] == 'available'
    assert measured['failed_files'] == 1
    assert measured['paths']['failed'] == ['changing.py']
    assert all(source['path'] != 'changing.py' for source in result['sources'])
    assert all(detail['path'] != 'changing.py' for detail in measured['file_details'])
    assert measured['read_files'] + measured['not_selected_files'] + measured['failed_files'] + measured['empty_files'] == measured['eligible_files']


def test_record_coverage_does_not_claim_summary_code_was_used_for_knowledge():
    bundle = {'manifest': {'entries': [{'source_key': 'one'}, {'source_key': 'two'}]},
              'coverage': {'processed': ['one'], 'unprocessed': ['one'], 'unavailable': ['two']}}
    diagnostics = {'source_selection': {'available_primary_fragments': 12,
                                        'selected_primary_fragments': 8, 'omitted_primary_fragments': 4}}
    result = knowledge_coverage(bundle, diagnostics)
    assert result['repository']['status'] == 'not_used'
    assert result['repository']['ai_input_files'] == 0
    assert result['conversations']['known_sources'] == 2
    assert result['conversations']['processed_sources'] == 1
    assert result['conversations']['partial_sources'] == 1
    assert result['conversations']['selected_fragments'] == 8
    assert result['conversations']['omitted_fragments'] == 4
    assert repository_coverage({})['status'] == 'unrecorded'


def test_coverage_metadata_does_not_invalidate_existing_repository_signature():
    old = {'sources': [{'id': 'code', 'text': 'Observed code'}], 'coverage': []}
    measured = {**deepcopy(old), 'inspection': {'read_files': 1}}
    legacy = hashlib.sha256(json.dumps(old, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    assert _repository_signature(old) == _repository_signature(measured) == legacy
