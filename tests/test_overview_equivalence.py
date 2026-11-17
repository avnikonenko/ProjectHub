"""Prompt and cache regressions using real SQLite and simulated model replies.

These checks establish instruction delivery and data preservation, not model
reasoning, scientific equivalence, or a live App Server protocol exchange.
"""
from copy import deepcopy

import pytest

from backend.app.integrations.codex import knowledge_brief as pilot
from backend.tests.integrations.test_knowledge_brief import (
    FakeSynthesis, _candidate, _stored, prepared,
)
from test_staged_overview_integration import StagedModel, _stage_rows, staged_state


class CapturedSynthesis(FakeSynthesis):
    def __init__(self):
        self.prompts = []

    def generate_brief(self, prompt, schema):
        self.prompts.append(prompt)
        return super().generate_brief(prompt, schema)


@pytest.mark.parametrize(('stage', 'kind', 'expected'), [
    (False, 'extracted_interpretation', True),
    (False, 'staged_interpretation', True),
    (True, 'extracted_interpretation', False),
    (True, 'staged_interpretation', True),
])
def test_equivalence_guidance_reaches_ordinary_and_combining_requests_only(prepared, stage, kind, expected):
    candidate = _candidate()
    repository = {'sources': [], 'coverage': []}
    sources, groups = pilot._brief_inputs(candidate, prepared.bundle, repository)
    sources[0]['kind'] = kind
    original = deepcopy((candidate, prepared.bundle, repository, sources, groups))
    manager = CapturedSynthesis()

    pilot._synthesize(manager, candidate, prepared.bundle, repository,
                      _sources=sources, _groups=groups, _stage=stage)

    assert len(manager.prompts) == 1
    assert bool(pilot.EQUIVALENCE_GUIDANCE)
    assert (pilot.EQUIVALENCE_GUIDANCE in manager.prompts[0]) is expected
    assert (candidate, prepared.bundle, repository, sources, groups) == original


@pytest.mark.parametrize('required', [
    'When original evidence explicitly explains equivalence under stated conditions',
    'attribute that equivalence to the discussion and retain those conditions',
    'do not claim independent proof or validation',
    'Missing acceptance of the optimization alone does not make the underlying policy unresolved or establish a conflict',
    'Describe the optimization as proposed with acceptance unverified when appropriate',
    'without promoting it to agreed or rewriting Knowledge statuses',
    'Do not infer equivalence from similar labels',
    'preserve genuine differences in outcomes, scope, requirements or unresolved validation questions',
])
def test_equivalence_instruction_contract_preserves_conditions_conflicts_and_acceptance(required):
    # A prompt contract test deliberately makes no claim about model compliance.
    assert required in pilot.EQUIVALENCE_GUIDANCE


def test_equivalence_prompt_change_reuses_all_leaf_caches_without_changing_knowledge(staged_state, monkeypatch):
    # This fixed limit yields sixteen leaf batches for the shared 520-item fixture.
    monkeypatch.setattr(pilot, 'MAX_SYNTHESIS_CHARS', 60_000)
    guidance = pilot.EQUIVALENCE_GUIDANCE
    monkeypatch.setattr(pilot, 'EQUIVALENCE_GUIDANCE', '')
    before = _stored(staged_state.engine)
    initial = StagedModel()
    first = pilot.generate_knowledge_brief(staged_state.engine, 1, initial, publish=False)
    assert len(initial.calls) == 17
    assert not any(source.get('kind') == 'staged_interpretation'
                   for payload in initial.calls[:16] for source in payload['sources'])
    assert all(source.get('kind') == 'staged_interpretation'
               for source in initial.calls[-1]['sources'])
    cached_before = {row.fingerprint: deepcopy(row.evidence) for row in _stage_rows(staged_state.engine)}
    assert len(cached_before) == 17

    monkeypatch.setattr(pilot, 'EQUIVALENCE_GUIDANCE', guidance)
    retry = StagedModel()
    second = pilot.generate_knowledge_brief(staged_state.engine, 1, retry, publish=False)

    assert second['diagnostics']['synthesis_cache_hits'] == 16
    assert second['diagnostics']['synthesis_requests'] == len(retry.calls) == 1
    assert all(source.get('kind') == 'staged_interpretation'
               for payload in retry.calls for source in payload['sources'])
    cached_after = {row.fingerprint: deepcopy(row.evidence) for row in _stage_rows(staged_state.engine)}
    assert len(cached_after) == 18
    assert all(cached_after[key] == value for key, value in cached_before.items())
    assert second['knowledge']['items'] == first['knowledge']['items'] == staged_state.originals[1]['items']
    assert staged_state.candidates == staged_state.originals
    assert _stored(staged_state.engine) == before
