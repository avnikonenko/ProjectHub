"""Deterministic staged-packing and citation tests; model output is mocked."""
from copy import deepcopy
import json

import pytest

from backend.app.integrations.codex import knowledge_brief
from backend.app.integrations.codex.brief import SECTION_NAMES
from backend.app.integrations.codex.protocol import CodexError
from backend.app.integrations.codex.staged_overview import _partition, _summary_sources


def test_balanced_packing_includes_every_unit_and_companion_without_prefix_rescanning():
    units = [[{'id': f'item:{i}', 'text': 'x' * 10}, {'id': 'context', 'text': 'completion'}]
             for i in range(1000)]
    original = deepcopy(units)
    calls = []

    def measure(records):
        calls.append(len(records))
        return sum(len(record['text']) for record in records)

    batches = _partition(units, measure, 110)
    visited = [record['id'] for batch in batches for record in batch if record['id'] != 'context']
    assert visited == [f'item:{i}' for i in range(1000)]
    assert all(sum(len(record['text']) for record in batch) <= 110 for batch in batches)
    assert all(sum(record['id'] == 'context' for record in batch) == 1 for batch in batches)
    assert len(calls) < 400
    assert units == original


def test_singleton_overflow_keeps_exact_support_unchanged():
    units = [[{'id': 'item', 'text': 'x' * 200}, {'id': 'context', 'text': 'later completion'}]]
    before = deepcopy(units)
    with pytest.raises(CodexError, match='One Overview item.*saved results are preserved'):
        _partition(units, lambda records: sum(len(record['text']) for record in records), 100)
    assert units == before


def test_stage_summary_preserves_only_known_original_quotes_and_all_support():
    originals = [{'id': f'original:{i}', 'role': 'user', 'text': f'Unrelated long fragment {i}. ' * 300,
                  'excerpts': [f'Exact selected evidence {i}.']} for i in range(5)]
    evidence = [{'source_id': original['id'], 'role': 'user', 'excerpt': original['excerpts'][0]}
                for original in originals]
    source = {'id': 'knowledge:combined', 'item': {'statement': 'Combined claim', 'evidence': evidence}}
    point = {'text': 'Combined claim.', 'classification': 'reported',
             'source_ids': [originals[0]['id']], 'supporting_source_ids': [s['id'] for s in originals]}
    result = ({'architecture': [point]}, {'nodes': [], 'edges': []}, originals)
    summaries, groups = _summary_sources(result, [source], {source['id']: originals}, 0, 0)
    item = summaries[0]['item']
    assert item['staged_support'] is True
    assert item['evidence'] == evidence
    assert item['section'] == 'architecture'
    assert groups[summaries[0]['id']] == originals
    assert 'Unrelated long fragment' not in json.dumps(summaries)


def test_staged_source_retains_more_than_three_actual_original_references():
    originals = [{'id': f'original:{i}', 'text': f'Original support {i}.', 'role': 'user'} for i in range(5)]
    source = {'id': 'overview-stage:0:0:0', 'kind': 'staged_interpretation',
              'item': {'statement': 'A supported compound point.', 'source_state': 'valid',
                       'agreement_basis': 'none', 'staged_support': True,
                       'evidence': [{'source_id': item['id'], 'role': item['role'], 'excerpt': item['text']}
                                    for item in originals]}}

    class Model:
        def generate_brief(self, prompt, schema):
            sections = {name: [] for name in SECTION_NAMES}
            for name in ('overview', 'architecture', 'current_status'):
                sections[name] = [{'text': 'A supported compound point.', 'classification': 'reported',
                                   'source_ids': ['k1']}]
            return json.dumps({'sections': sections, 'project_map': {'nodes': [], 'edges': []}})

    sections, _, cited = knowledge_brief._synthesize(
        Model(), {'coverage': {}}, {'project': {}}, {}, _sources=[source],
        _groups={source['id']: originals}, _stage=True)
    assert sections['overview'][0]['source_ids'] == [item['id'] for item in originals]
    assert cited == originals
    assert all(not item['id'].startswith('overview-stage:') for item in cited)


def test_stage_summary_rejects_quote_missing_from_authoritative_originals():
    original = {'id': 'original:a', 'text': 'Known source.', 'excerpts': ['Known source.']}
    source = {'id': 'knowledge:a', 'item': {'evidence': [{'source_id': original['id'], 'excerpt': 'Invented support.'}]}}
    result = ({'overview': [{'text': 'Claim.', 'source_ids': [original['id']]}]}, {}, [original])
    with pytest.raises(CodexError, match='missing or changed original evidence'):
        _summary_sources(result, [source], {source['id']: [original]}, 0, 0)


def test_packing_rejects_same_identity_with_conflicting_content():
    units = [[{'id': 'same', 'text': 'first'}], [{'id': 'same', 'text': 'different'}]]
    with pytest.raises(CodexError, match='conflicting records for the same identity'):
        _partition(units, lambda records: 1, 100)


def test_staged_map_relationships_use_component_labels_and_endpoint_support():
    original = {'id': 'original:a', 'text': 'The interface calls the backend.'}
    source = {'id': 'knowledge:a', 'item': {'evidence': [{'source_id': original['id'], 'excerpt': original['text']}]}}
    graph = {'nodes': [{'id': 'n1', 'label': 'Web interface', 'source_ids': [original['id']]},
                       {'id': 'n2', 'label': 'Project API', 'source_ids': [original['id']]}],
             'edges': [{'from': 'n1', 'to': 'n2', 'label': 'calls', 'inferred': True, 'source_ids': [original['id']]}]}
    summaries, _ = _summary_sources(({}, graph, [original]), [source], {source['id']: [original]}, 0, 0)
    assert summaries[-1]['item']['statement'] == 'Web interface → Project API: calls'


def test_failed_second_leaf_reports_only_successfully_processed_original_items(monkeypatch):
    originals = [{'id': f'original:{i}', 'role': 'user', 'text': f'Original evidence {i}.'} for i in range(12)]
    candidate = {'project_id': 1, 'coverage': {}, 'sources': originals,
                 'items': [{'id': f'item:{i}', 'statement': f'Component {i}: ' + 'x' * 1500,
                            'source_state': 'valid', 'evidence': [{'source_id': source['id'], 'role': 'user',
                                                                'excerpt': source['text']}]}
                           for i, source in enumerate(originals)]}
    monkeypatch.setattr(knowledge_brief, 'MAX_SYNTHESIS_CHARS', 4000)

    class Model:
        calls = 0
        first_items = 0

        def generate_brief(self, prompt, schema):
            self.calls += 1
            if self.calls == 2:
                raise CodexError('Second batch failed.')
            payload = json.loads(prompt.split('\n', 1)[1])
            self.first_items = sum('item' in source for source in payload['sources'])
            sections = {name: [] for name in SECTION_NAMES}
            for name in ('overview', 'architecture', 'current_status'):
                sections[name] = [{'text': 'A supported project point.', 'classification': 'reported',
                                   'source_ids': ['k1']}]
            return json.dumps({'sections': sections, 'project_map': {'nodes': [], 'edges': []}})

    model, diagnostics = Model(), {}
    with pytest.raises(CodexError, match='Second batch failed'):
        knowledge_brief._synthesize(model, candidate, {'project': {'id': 1}}, {},
                                   selection_diagnostics=diagnostics)
    assert diagnostics['selected_items'] == model.first_items
    assert 0 < diagnostics['selected_items'] < diagnostics['available_items'] == 12
    assert diagnostics['omitted_items'] == 0
    assert diagnostics['synthesis_requests'] == 2
    assert diagnostics['synthesis_batches_completed'] == 1


def test_stage_summary_does_not_expand_to_uncited_quotes_from_same_original_fragment():
    original = {'id': 'original:a', 'text': 'Chosen fact. Different fact.',
                'excerpts': ['Chosen fact.', 'Different fact.']}
    sources = [{'id': f'knowledge:{i}', 'item': {'evidence': [
        {'source_id': original['id'], 'role': 'user', 'excerpt': quote}]}}
        for i, quote in enumerate(original['excerpts'])]
    point = {'text': 'Chosen fact.', 'classification': 'reported', 'source_ids': [original['id']]}
    result = ({'overview': [point]}, {}, [original])
    summaries, _ = _summary_sources(result, sources, {source['id']: [original] for source in sources},
                                    0, 0, {id(point): ['knowledge:0']})
    assert [ref['excerpt'] for ref in summaries[0]['item']['evidence']] == ['Chosen fact.']


def test_replay_reuses_cache_when_live_style_reply_metadata_appears(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session
    from backend.app.db import initialize
    from backend.app.models import Project

    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add(Project(id=1, name='Synthetic')); db.commit()
    originals = [{'id': f'original:{i}', 'role': 'user', 'text': f'Original evidence {i}.'} for i in range(12)]
    candidate = {'project_id': 1, 'coverage': {}, 'sources': originals,
                 'items': [{'id': f'item:{i}', 'statement': f'Component {i}: ' + 'x' * 1500,
                            'source_state': 'valid', 'evidence': [{'source_id': source['id'], 'role': 'user',
                                                                'excerpt': source['text']}]}
                           for i, source in enumerate(originals)]}
    monkeypatch.setattr(knowledge_brief, 'MAX_SYNTHESIS_CHARS', 8000)

    class LiveStyleModel:
        calls = 0

        def for_generation(self, model, effort=None):
            return self

        def generate_brief(self, prompt, schema):
            self.calls += 1
            # Real Codex managers fill these after the provider resolves defaults.
            self.generation_metadata = {'account': 'resolved-account', 'model': 'resolved-model', 'effort': 'medium'}
            sections = {name: [] for name in SECTION_NAMES}
            for name in ('overview', 'architecture', 'current_status'):
                sections[name] = [{'text': 'A supported project point.', 'classification': 'reported',
                                   'source_ids': ['k1']}]
            return json.dumps({'sections': sections, 'project_map': {'nodes': [], 'edges': []}})

    manager = LiveStyleModel()
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'profile-a'))
    try:
        first = {}
        knowledge_brief._synthesize(manager, candidate, {'project': {'id': 1, 'name': 'Synthetic'}}, {},
                                   selection_diagnostics=first, engine=engine, project_id=1)
        calls = manager.calls
        assert calls > 1
        replay = {}
        knowledge_brief._synthesize(manager, candidate, {'project': {'id': 1, 'name': 'Synthetic'}}, {},
                                   selection_diagnostics=replay, engine=engine, project_id=1)
        assert manager.calls == calls
        assert replay['synthesis_requests'] == 0
        assert replay['synthesis_cache_hits'] == calls
        monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'profile-b'))
        other_account = {}
        knowledge_brief._synthesize(manager, candidate, {'project': {'id': 1, 'name': 'Synthetic'}}, {},
                                   selection_diagnostics=other_account, engine=engine, project_id=1)
        assert manager.calls > calls
        assert other_account['synthesis_requests'] > 0
        calls = manager.calls
        monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'profile-a'))
        original_account = {}
        knowledge_brief._synthesize(manager, candidate, {'project': {'id': 1, 'name': 'Synthetic'}}, {},
                                   selection_diagnostics=original_account, engine=engine, project_id=1)
        assert manager.calls == calls
        assert original_account['synthesis_requests'] == 0
        manager.generation_model = 'another-requested-model'
        changed = {}
        knowledge_brief._synthesize(manager, candidate, {'project': {'id': 1, 'name': 'Synthetic'}}, {},
                                   selection_diagnostics=changed, engine=engine, project_id=1)
        assert manager.calls > calls
        assert changed['synthesis_requests'] > 0
    finally:
        engine.dispose()


def test_metadata_only_adapter_fallback_still_binds_declared_identity():
    from types import SimpleNamespace
    from backend.app.integrations.codex.staged_overview import _requested_provenance

    first = SimpleNamespace(generation_metadata={'account': 'adapter', 'model': 'model-a', 'effort': 'low'})
    second = SimpleNamespace(generation_metadata={'account': 'adapter', 'model': 'model-b', 'effort': 'low'})
    assert _requested_provenance(first)['model'] == 'model-a'
    assert _requested_provenance(second)['model'] == 'model-b'


def test_requested_account_home_prefers_explicit_paths_over_launch_directory(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.app.integrations.codex.staged_overview import _requested_provenance

    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'launch'))
    manager = SimpleNamespace(for_generation=lambda *args: None, account_key='registered:1',
                              generation_account=tmp_path / 'selected', account_home=tmp_path / 'account')
    assert _requested_provenance(manager)['account_home'] == str(tmp_path / 'selected')
    assert _requested_provenance(manager)['account'] == str(tmp_path / 'selected')
    manager.generation_account = None
    assert _requested_provenance(manager)['account_home'] == str(tmp_path / 'account')
    manager.account_home = None
    assert _requested_provenance(manager)['account_home'] == str(tmp_path / 'launch')
    assert _requested_provenance(manager)['account'] == 'registered:1'
