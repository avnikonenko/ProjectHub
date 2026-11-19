"""Profile budgeting and cache contracts; model replies are mocked, sources literal."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import hashlib
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, ProjectHistoryBatch
from backend.app.generation_profiles import get_generation_profile
from backend.app.integrations.codex import knowledge
from backend.app.integrations.codex.ai_instructions import ai_instructions, use_project_instructions
from backend.app.integrations.codex.knowledge_profiles import (
    captured_knowledge_workers, configure_profile_manager, current_knowledge_profile,
    profile_cache_settings, select_profile_windows, use_knowledge_profile,
)


def source(identifier, text, conversation='A', order=1):
    return {'id': identifier, 'role': 'assistant', 'text': text,
            'revision': hashlib.sha256(text.encode()).hexdigest(), 'order': order,
            'conversation_id': conversation, 'source_key': conversation, 'label': identifier}


def window(primary, context=()):
    return {'sources': [*context, primary], 'primary_ids': [primary['id']],
            'context_ids': [value['id'] for value in context]}


def bundle(sources):
    return {'project': {'id': 1}, 'sources': sources,
            'manifest': {'fingerprint': 'unchanged', 'entries': [
                {'source_key': key, 'availability': 'available', 'revision': key}
                for key in dict.fromkeys(value['source_key'] for value in sources)]},
            'coverage': {'processed': list(dict.fromkeys(value['source_key'] for value in sources)),
                         'unprocessed': [], 'unavailable': [], 'excluded': [], 'limitations': []}}


def test_budget_selects_whole_windows_fairly_and_retains_source_order():
    sources = [source('a1', '1111'), source('a2', '2222', order=2),
               source('b1', '3333', 'B'), source('b2', '4444', 'B', 2)]
    original = bundle(sources)
    windows = [window(value) for value in sources]
    before = deepcopy((original, windows))
    economy = replace(get_generation_profile('economy'), source_budget_chars=8)
    selected, coverage, selection = select_profile_windows(original, windows, economy)
    assert [value['primary_ids'] for value in selected] == [['a1'], ['b1']]
    assert selection == {'available_primary_fragments': 4, 'selected_primary_fragments': 2,
                         'context_only_fragments': 0, 'omitted_primary_fragments': 2,
                         'selected_source_chars': 8, 'partial': True}
    assert coverage['not_extracted_by_profile'] == ['a2', 'b2']
    assert any('partial extraction coverage' in value for value in coverage['limitations'])
    assert (original, windows) == before
    coverage['processed'].append('caller change')
    assert original == before[0]


def test_context_counts_toward_budget_and_cannot_be_trimmed_to_admit_a_window():
    proposal = source('proposal', '12345')
    acceptance = {**source('acceptance', 'Yes.', order=2), 'role': 'user'}
    unrelated = source('unrelated', 'xyz', 'B')
    windows = [window(acceptance, [proposal]), window(unrelated)]
    economy = replace(get_generation_profile('economy'), source_budget_chars=8)
    selected, coverage, selection = select_profile_windows(bundle([proposal, acceptance, unrelated]), windows, economy)
    assert selected == [windows[1]]  # The complete proposal+acceptance costs nine.
    assert selection['selected_source_chars'] == 3
    assert selection['omitted_primary_fragments'] == 1
    assert coverage['not_extracted_by_profile'] == ['acceptance']
    assert windows[0]['sources'] == [proposal, acceptance]


def test_overview_citations_prioritize_original_windows_within_each_conversation():
    sources = [source('a1', '1111'), source('a2', '2222', order=2),
               source('b1', '3333', 'B'), source('b2', '4444', 'B', 2)]
    economy = replace(get_generation_profile('economy'), source_budget_chars=8)
    selected, coverage, _ = select_profile_windows(
        bundle(sources), [window(value) for value in sources], economy,
        overview_source_ids={'a2', 'b2'},
    )
    assert [value['sources'] for value in selected] == [[sources[1]], [sources[3]]]
    assert coverage['not_extracted_by_profile'] == ['a1', 'b1']


def test_context_is_counted_once_across_selected_windows():
    first = source('first', '123')
    second = source('second', '45678', order=2)
    windows = [window(first), window(second, [first])]
    selected, coverage, selection = select_profile_windows(
        bundle([first, second]), windows,
        replace(get_generation_profile('economy'), source_budget_chars=8))
    assert selected == windows and selection['selected_source_chars'] == 8
    assert selection['context_only_fragments'] == 0
    assert not selection['partial'] and 'not_extracted_by_profile' not in coverage


def test_context_only_fragments_are_reported_without_becoming_primary():
    first = source('first', '123')
    second = source('second', '45678', order=2)
    selected, _, selection = select_profile_windows(
        bundle([first, second]), [window(second, [first])],
        replace(get_generation_profile('economy'), source_budget_chars=8))
    assert selected[0]['primary_ids'] == ['second']
    assert selection['selected_primary_fragments'] == 1
    assert selection['context_only_fragments'] == 1
    assert selection['selected_source_chars'] == 8


@pytest.mark.parametrize('profile_name', ['quality', 'balanced'])
def test_full_coverage_profiles_keep_all_windows_and_manifest(profile_name):
    sources = [source('a', '12345'), source('b', 'abcdefgh', 'B')]
    original = bundle(sources)
    windows = [window(value) for value in sources]
    before = deepcopy(original)
    selected, coverage, selection = select_profile_windows(original, windows, get_generation_profile(profile_name))
    assert selected == windows and not selection['partial']
    assert selection['selected_primary_fragments'] == 2
    assert selection['selected_source_chars'] == 13
    assert coverage == original['coverage'] and original == before


def test_no_window_fits_has_explicit_partial_coverage_and_unchanged_manifest():
    value = source('large', 'a' * 10)
    original = bundle([value])
    before = deepcopy(original)
    selected, coverage, selection = select_profile_windows(
        original, [window(value)], replace(get_generation_profile('economy'), source_budget_chars=9))
    assert selected == []
    assert selection['partial'] and selection['selected_source_chars'] == 0
    assert coverage['not_extracted_by_profile'] == ['large']
    assert original == before


@pytest.mark.parametrize('profile_name', ['quality', 'balanced', 'economy'])
def test_empty_source_set_is_not_reported_as_budget_omission(profile_name):
    original = bundle([])
    selected, coverage, selection = select_profile_windows(original, [], get_generation_profile(profile_name))
    assert selected == [] and coverage == original['coverage']
    assert not selection['partial'] and selection['omitted_primary_fragments'] == 0


def test_quality_preserves_legacy_extraction_configuration_and_profile_caches_are_separate(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='Profile cache contract')); db.commit()
    original = source('source', 'Consider a controlled experiment.')
    batch = window(original)

    class Manager:
        generation_account = 'test-profile'
        generation_model = 'test-model'
        generation_effort = 'medium'
        calls = 0
        def generate_brief(self, prompt, schema):
            self.calls += 1
            return json.dumps({'items': [{
                'statement': original['text'], 'category': 'PROPOSAL', 'design_status': 'PROPOSED',
                'scope': 'project', 'rationale': '', 'alternatives': '', 'limitations': '',
                'agreement_basis': 'none', 'revises_statement': '', 'conflicting_statements': [],
                'evidence': [{'source_id': original['id'], 'role': original['role'], 'excerpt': original['text']}],
            }]})

    manager = Manager()
    with use_project_instructions('Controlled request instructions.'):
        legacy = {'schema': knowledge.SCHEMA_VERSION, 'prompt': knowledge.PROMPT_VERSION,
                  'instructions': ai_instructions(), 'account': 'test-profile',
                  'model': 'test-model', 'effort': 'medium'}
        # Seed the exact pre-profile contract, not a new profile-aware key.
        old_items, hit = knowledge.extract_batch(engine, 1, manager, batch, legacy)
        assert not hit and manager.calls == 1
        with use_knowledge_profile(get_generation_profile('quality')):
            assert profile_cache_settings(current_knowledge_profile()) is None
            shared = {name: value for name, value in legacy.items() if name != 'account'}
            assert knowledge.extraction_configuration(manager) == shared
            replay, hit = knowledge.extract_batch(engine, 1, manager, batch, knowledge.extraction_configuration(manager))
            upgraded_items = [{**item, 'extraction_version': knowledge._digest(shared)} for item in old_items]
            assert hit and replay == upgraded_items and manager.calls == 1
        versions = {upgraded_items[0]['extraction_version']}
        for name in ('balanced', 'economy'):
            with use_knowledge_profile(get_generation_profile(name)):
                configuration = knowledge.extraction_configuration(manager)
                assert configuration['generation_profile']['id'] == name
                assert configuration['generation_profile']['selection_policy'] == 'whole_windows_round_robin_v1'
                items, hit = knowledge.extract_batch(engine, 1, manager, batch, configuration)
                assert not hit
                assert items[0]['id'] == old_items[0]['id']
                versions.add(items[0]['extraction_version'])
        assert len(versions) == 3 and manager.calls == 3
        with use_knowledge_profile(get_generation_profile('quality')):
            replay, hit = knowledge.extract_batch(engine, 1, manager, batch, knowledge.extraction_configuration(manager))
            assert hit and replay == upgraded_items and manager.calls == 3
    with Session(engine) as db:
        assert len(db.scalars(select(ProjectHistoryBatch)).all()) == 4
    engine.dispose()


def test_captured_profile_and_workers_survive_settings_changes_and_worker_context_copy(monkeypatch):
    quality = get_generation_profile('quality')
    manager = SimpleNamespace(independent_generation=lambda: None)
    monkeypatch.setenv('PROJECTHUB_GENERATION_PROFILE', 'quality')
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '2')
    with use_knowledge_profile(quality, workers=2):
        baseline = knowledge.extraction_configuration(SimpleNamespace())
        monkeypatch.setenv('PROJECTHUB_GENERATION_PROFILE', 'economy')
        monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '12')
        def inspect():
            return current_knowledge_profile(), captured_knowledge_workers(), knowledge._extraction_workers(manager)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(copy_context().run, inspect) for _ in range(2)]
            assert all(result.result() == (quality, 2, 2) for result in results)
        assert knowledge.extraction_configuration(SimpleNamespace()) == baseline
        with use_knowledge_profile(get_generation_profile('balanced'), workers=3):
            assert knowledge._extraction_workers(manager) == 3
            assert current_knowledge_profile().id == 'balanced'
        assert current_knowledge_profile() is quality and knowledge._extraction_workers(manager) == 2
    assert current_knowledge_profile().id == 'economy'
    assert captured_knowledge_workers() is None and knowledge._extraction_workers(manager) == 12


def test_profile_context_resets_after_failure(monkeypatch):
    monkeypatch.setenv('PROJECTHUB_GENERATION_PROFILE', 'quality')
    with pytest.raises(RuntimeError):
        with use_knowledge_profile(get_generation_profile('economy'), workers=7):
            raise RuntimeError('Injected request failure')
    assert current_knowledge_profile().id == 'quality' and captured_knowledge_workers() is None


def test_economy_reasoning_default_never_overrides_explicit_choice_or_model():
    model = SimpleNamespace(generation_model='chosen-model', generation_effort='high')
    selected, details = configure_profile_manager(model, get_generation_profile('economy'))
    assert selected is model and selected.generation_model == 'chosen-model' and selected.generation_effort == 'high'
    assert any('explicit reasoning effort' in note for note in details['notes'])
    default_model = SimpleNamespace()
    selected, details = configure_profile_manager(default_model, get_generation_profile('economy'))
    assert selected is default_model and details['effective_effort'] is None
    assert any('Codex default model' in note for note in details['notes'])


def test_economy_reasoning_default_requires_advertised_support():
    calls = []
    def selected(model, effort):
        calls.append((model, effort))
        return SimpleNamespace(generation_model=model, generation_effort=effort)
    manager = SimpleNamespace(generation_model='chosen-model', generation_effort=None,
                              generation_models=lambda: [{'id': 'chosen-model', 'efforts': ['low', 'medium']}],
                              for_generation=selected)
    result, details = configure_profile_manager(manager, get_generation_profile('economy'))
    assert calls == [('chosen-model', 'low')]
    assert result.generation_model == 'chosen-model' and details['effective_effort'] == 'low'
    manager.generation_models = lambda: [{'id': 'chosen-model', 'efforts': ['medium']}]
    result, details = configure_profile_manager(manager, get_generation_profile('economy'))
    assert result is manager and len(calls) == 1
    assert any('does not advertise' in note for note in details['notes'])
