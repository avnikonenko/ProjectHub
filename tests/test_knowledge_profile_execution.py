"""Profile execution and diagnostics invariants with mocked semantic generation."""
import copy
from copy import deepcopy

import pytest
from sqlalchemy.orm import Session

from backend.app.generation_profiles import get_generation_profile
from backend.app.integrations.codex import knowledge_brief as pilot, progress
from backend.app.integrations.codex.generation_metrics import begin_model_request
from backend.app.integrations.codex.knowledge import extraction_configuration, _extraction_workers
from backend.app.integrations.codex.knowledge_previews import read_knowledge_preview
from backend.app.integrations.codex.knowledge_profiles import (
    configure_profile_manager, current_knowledge_profile, use_knowledge_profile,
)
from backend.app.integrations.codex.protocol import CodexError
from backend.tests.integrations.test_knowledge_brief import prepared, FakeSynthesis, _stored


class ProfileManager(FakeSynthesis):
    generation_model = 'selected-model'
    generation_effort = None

    def __init__(self, efforts=('low', 'medium', 'high')):
        self.efforts = list(efforts)
        self.catalog_calls = 0
        self.selection_calls = []

    def generation_models(self):
        self.catalog_calls += 1
        return [{'id': self.generation_model, 'efforts': list(self.efforts)}]

    def for_generation(self, model, effort):
        self.selection_calls.append((model, effort))
        result = copy.copy(self)
        result.generation_model = model
        result.generation_effort = effort
        return result


@pytest.mark.parametrize('profile', ['quality', 'balanced'])
def test_quality_and_balanced_preserve_manual_model_and_effort(profile):
    manager = ProfileManager()
    manager.generation_effort = 'high'
    selected, info = configure_profile_manager(manager, get_generation_profile(profile))
    assert selected is manager
    assert selected.generation_model == 'selected-model'
    assert selected.generation_effort == info['requested_effort'] == info['effective_effort'] == 'high'
    assert manager.catalog_calls == 0 and manager.selection_calls == []


def test_economy_uses_low_only_when_supported_and_effort_is_unselected():
    manager = ProfileManager()
    selected, info = configure_profile_manager(manager, get_generation_profile('economy'))
    assert selected is not manager
    assert manager.generation_model == selected.generation_model == 'selected-model'
    assert manager.generation_effort is None
    assert selected.generation_effort == 'low'
    assert info['requested_effort'] is None and info['effective_effort'] == 'low'
    assert manager.selection_calls == [('selected-model', 'low')]


@pytest.mark.parametrize('effort', ['medium', 'high', 'low'])
def test_explicit_effort_wins_over_economy_and_no_catalog_request_is_needed(effort):
    manager = ProfileManager()
    manager.generation_effort = effort
    selected, info = configure_profile_manager(manager, get_generation_profile('economy'))
    assert selected is manager and selected.generation_effort == effort
    assert info['requested_effort'] == info['effective_effort'] == effort
    assert manager.catalog_calls == 0 and manager.selection_calls == []
    assert any('explicit reasoning effort' in note for note in info['notes'])


def test_economy_does_not_replace_codex_default_model_or_guess_its_efforts():
    manager = ProfileManager()
    manager.generation_model = None
    selected, info = configure_profile_manager(manager, get_generation_profile('economy'))
    assert selected is manager and selected.generation_model is None and selected.generation_effort is None
    assert manager.catalog_calls == 0 and manager.selection_calls == []
    assert any('Codex default model' in note for note in info['notes'])


def test_economy_preserves_model_default_when_low_is_not_advertised():
    manager = ProfileManager(efforts=('medium', 'high'))
    selected, info = configure_profile_manager(manager, get_generation_profile('economy'))
    assert selected is manager and selected.generation_effort is None
    assert manager.selection_calls == []
    assert any('does not advertise' in note for note in info['notes'])


def test_economy_reports_catalog_failure_instead_of_silently_guessing():
    class Unavailable(ProfileManager):
        def generation_models(self):
            raise CodexError('Model catalog disconnected')

    manager = Unavailable()
    with pytest.raises(CodexError, match='catalog disconnected'):
        configure_profile_manager(manager, get_generation_profile('economy'))
    assert manager.generation_model == 'selected-model' and manager.generation_effort is None


def test_profile_and_worker_capture_are_immutable_during_runtime_setting_changes(monkeypatch):
    monkeypatch.setenv('PROJECTHUB_GENERATION_PROFILE', 'quality')
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '4')
    manager = ProfileManager()
    manager.independent_generation = lambda: None
    with use_knowledge_profile(get_generation_profile('quality'), 4):
        original = extraction_configuration(manager)
        monkeypatch.setenv('PROJECTHUB_GENERATION_PROFILE', 'economy')
        monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '27')
        assert current_knowledge_profile().id == 'quality'
        assert _extraction_workers(manager) == 4
        assert extraction_configuration(manager) == original
    assert current_knowledge_profile().id == 'economy'
    assert _extraction_workers(manager) == 27


class RecordedSynthesis(FakeSynthesis):
    def generate_brief(self, prompt, schema):
        request = begin_model_request('synthetic-metric-thread')
        assert request is not None, 'Knowledge generation did not establish its metrics scope'
        request.bind_turn('synthetic-turn')
        request.record({'threadId': 'synthetic-metric-thread', 'turnId': 'synthetic-turn',
                        'tokenUsage': {'total': {'totalTokens': 120, 'inputTokens': 100,
                        'outputTokens': 20, 'cachedInputTokens': 30, 'reasoningOutputTokens': 4}}})
        return super().generate_brief(prompt, schema)


def settings(monkeypatch, profile='quality'):
    values = {'generation_profile': profile, 'extraction_workers': 4}
    monkeypatch.setattr(pilot, 'ai_worker_settings', lambda: dict(values))
    return values


def test_saved_preview_run_diagnostics_are_detached_and_do_not_change_published_pair(prepared, monkeypatch):
    settings(monkeypatch)
    before = _stored(prepared.engine)
    result = pilot.generate_knowledge_brief(prepared.engine, 1, RecordedSynthesis())
    expected = deepcopy(result['diagnostics'])
    expected_records = deepcopy(result['knowledge']['diagnostics'])
    assert expected_records['evidence_coverage']['repository']['status'] == 'not_used'
    assert expected['evidence_coverage']['repository']['status'] == 'not_linked'
    assert {key: value for key, value in expected_records.items() if key != 'evidence_coverage'} == {
        key: value for key, value in expected.items() if key != 'evidence_coverage'}
    assert expected['profile']['id'] == 'quality'
    assert expected['model_requests'] == 1  # Tagging is a separate explicit action.
    assert expected['token_usage']['total_tokens'] == 120
    assert expected['token_usage']['status'] == 'complete'
    result['diagnostics']['token_usage']['total_tokens'] = 9000
    result['diagnostics']['profile']['notes'].append('caller mutation')
    assert result['knowledge']['diagnostics'] == expected_records
    assert result['brief']['diagnostics'] == expected
    status = progress.read(1, 'knowledge')
    assert status['state'] == 'completed' and status['diagnostics'] == expected
    status['diagnostics']['profile']['notes'].append('progress caller mutation')
    assert progress.read(1, 'knowledge')['diagnostics'] == expected
    with Session(prepared.engine) as db:
        assert read_knowledge_preview(db, 1)['diagnostics'] == expected
    assert _stored(prepared.engine) == before


def test_failed_run_keeps_usage_diagnostics_and_previous_preview(prepared, monkeypatch):
    settings(monkeypatch)
    previous = pilot.generate_knowledge_brief(prepared.engine, 1, RecordedSynthesis())
    before = _stored(prepared.engine)
    manager = RecordedSynthesis()
    manager.fail = True
    with pytest.raises(CodexError, match='Mock synthesis unavailable'):
        pilot.generate_knowledge_brief(prepared.engine, 1, manager)
    state = progress.read(1, 'knowledge')
    assert state['state'] == 'failed'
    assert state['diagnostics']['model_requests'] == 1
    assert state['diagnostics']['token_usage']['total_tokens'] == 120
    with Session(prepared.engine) as db:
        assert read_knowledge_preview(db, 1) == previous
    assert _stored(prepared.engine) == before


def test_late_save_failure_reports_current_duration_not_earlier_candidate_duration(prepared, monkeypatch):
    settings(monkeypatch)
    previous = pilot.generate_knowledge_brief(prepared.engine, 1, RecordedSynthesis())
    clock = {'now': 100.0}
    monkeypatch.setattr(pilot.time, 'monotonic', lambda: clock['now'])
    original = pilot.stage_knowledge_preview

    def fail_after_staging(db, project_id, result):
        assert result['diagnostics']['duration_seconds'] == 0
        original(db, project_id, result)
        clock['now'] = 105.0
        raise CodexError('Late preview save failed')

    monkeypatch.setattr(pilot, 'stage_knowledge_preview', fail_after_staging)
    with pytest.raises(CodexError, match='Late preview save failed'):
        pilot.generate_knowledge_brief(prepared.engine, 1, RecordedSynthesis())
    assert progress.read(1, 'knowledge')['diagnostics']['duration_seconds'] == 5
    with Session(prepared.engine) as db:
        assert read_knowledge_preview(db, 1) == previous


def test_cancellation_keeps_diagnostics_and_cannot_save_a_replacement(prepared, monkeypatch):
    settings(monkeypatch)
    previous = pilot.generate_knowledge_brief(prepared.engine, 1, RecordedSynthesis())
    before = _stored(prepared.engine)
    original = pilot.stage_knowledge_preview

    def cancel_before_commit(db, project_id, result):
        original(db, project_id, result)
        progress.cancel(project_id, 'knowledge', progress.read(project_id, 'knowledge')['started_at'])

    monkeypatch.setattr(pilot, 'stage_knowledge_preview', cancel_before_commit)
    with pytest.raises(progress.GenerationCancelled):
        pilot.generate_knowledge_brief(prepared.engine, 1, RecordedSynthesis())
    state = progress.read(1, 'knowledge')
    assert state['state'] == 'cancelled'
    assert state['diagnostics']['token_usage']['total_tokens'] == 120
    with Session(prepared.engine) as db:
        assert read_knowledge_preview(db, 1) == previous
    assert _stored(prepared.engine) == before


def test_progress_diagnostic_reads_and_cancellation_are_deep_copies():
    original = {'profile': {'id': 'quality', 'notes': []}, 'token_usage': {'total_tokens': 12}}
    progress.begin(991, 'knowledge')
    try:
        progress.record_diagnostics(991, original)
        original['token_usage']['total_tokens'] = 999
        status = progress.read(991, 'knowledge')
        assert status['diagnostics']['token_usage']['total_tokens'] == 12
        status['diagnostics']['profile']['notes'].append('read mutation')
        cancelled = progress.cancel(991, 'knowledge', status['started_at'])
        cancelled['diagnostics']['profile']['notes'].append('cancel mutation')
        assert progress.read(991, 'knowledge')['diagnostics']['profile']['notes'] == []
        progress.record_diagnostics(992, {'token_usage': {'total_tokens': 8000}})
        assert 'diagnostics' not in progress.read(992, 'knowledge')
    finally:
        progress.finish(991)


def test_bad_profile_does_not_mutate_published_data_or_leave_generation_locked(prepared, monkeypatch):
    state = settings(monkeypatch, 'unsupported-profile')
    before = _stored(prepared.engine)
    with pytest.raises(CodexError, match='profile and worker settings'):
        pilot.generate_knowledge_brief(prepared.engine, 1, RecordedSynthesis())
    assert _stored(prepared.engine) == before
    state['generation_profile'] = 'quality'
    assert pilot.generate_knowledge_brief(prepared.engine, 1, RecordedSynthesis())['published'] is False
