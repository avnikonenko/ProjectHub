"""Combining policy changes invalidate only summaries, not complete source stages.

Synthetic models establish routing and cache boundaries, not semantic accuracy.
"""
import json

from backend.app.integrations.codex import knowledge_brief as pilot
from backend.tests.integrations.test_knowledge_brief import prepared
from tests.test_staged_overview_integration import StagedModel, staged_state


def _combining(payload):
    return any(source.get('kind') == 'staged_interpretation' for source in payload['sources'])


class ScopeInspect(StagedModel):
    def generate_brief(self, prompt, schema):
        payload = json.loads(prompt.rsplit('\n', 1)[1])
        guard = 'compare configuration modes, lifecycle phases and populations'
        assert (guard in prompt) == _combining(payload)
        return super().generate_brief(prompt, schema)


def test_combination_guidance_does_not_change_or_repeat_original_source_stages(staged_state, monkeypatch):
    first = ScopeInspect()
    pilot.generate_knowledge_brief(staged_state.engine, 1, first)
    leaf_calls = sum(not _combining(payload) for payload in first.calls)
    assert leaf_calls > 1
    original_synthesize = pilot._synthesize

    class ChangedCombination:
        def __init__(self, delegate):
            self.delegate = delegate

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        def generate_brief(self, prompt, schema):
            return self.delegate.generate_brief('Test-only combination-policy revision. ' + prompt, schema)

    def revised(manager, candidate, bundle, repository, **options):
        if options.get('_stage') and any(
                source.get('kind') == 'staged_interpretation' for source in options.get('_sources', [])):
            manager = ChangedCombination(manager)
        return original_synthesize(manager, candidate, bundle, repository, **options)

    monkeypatch.setattr(pilot, '_synthesize', revised)
    second = ScopeInspect()
    result = pilot.generate_knowledge_brief(staged_state.engine, 1, second)
    assert second.calls and all(_combining(payload) for payload in second.calls)
    assert result['diagnostics']['synthesis_cache_hits'] == leaf_calls
    assert result['diagnostics']['synthesis_requests'] == len(second.calls)
    assert result['knowledge']['items'] == staged_state.candidates[1]['items']
