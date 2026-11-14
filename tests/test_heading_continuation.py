"""Exact context views and conservative handling of legacy heading-only claims."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

from backend.app.generation_profiles import get_generation_profile
from backend.app.integrations.codex import knowledge as k
from backend.app.integrations.codex.knowledge_profiles import select_profile_windows
from backend.app.integrations.codex.knowledge_sources import build_windows, heading_continuation


def sources():
    texts = ['Earlier context. ' * 85 + '\n\n4. Keep the ligand during protonation assessment\n\n',
             'For preparing a holo complex, generally yes: assess protonation with the intended ligand present.\n\n'
             + 'Additional context. ' * 75]
    result, offset = [], 0
    for index, text in enumerate(texts):
        result.append({'id': f'message:p:{index + 1}', 'role': 'assistant', 'text': text,
                       'revision': hashlib.sha256(text.encode()).hexdigest(),
                       'conversation_id': 'conversation', 'source_key': 'conversation',
                       'message_revision': 'original-message-hash', 'message_order': 2,
                       'partial_message': True, 'order': index,
                       'offset_start': offset, 'offset_end': offset + len(text)})
        offset += len(text)
    return result


def bundle(refs):
    return {'sources': refs, 'project': {'id': 1},
            'manifest': {'entries': [{'source_key': 'conversation', 'availability': 'available'}]},
            'coverage': {'processed': ['conversation'], 'limitations': []}}


def test_heading_forward_context_is_exact_bounded_and_never_primary_twice():
    refs = sources()
    before = deepcopy(refs)
    windows = build_windows(bundle(refs), max_chars=3000)
    assert len(windows) == 2
    view = windows[0]['sources'][-1]
    assert view['id'] == refs[1]['id'] and view['context_view'] == 'heading-continuation-v1'
    assert view['text'] == refs[1]['text'].split('\n\n')[0]
    assert view['revision'] == refs[1]['revision']
    assert view['offset_end'] == view['offset_start'] + len(view['text'])
    assert [identifier for window in windows for identifier in window['primary_ids']] == [r['id'] for r in refs]
    assert all(len(json.dumps(window, ensure_ascii=False)) <= 3000 for window in windows)
    assert refs == before


@pytest.mark.parametrize('change', [
    {'role': 'user'}, {'conversation_id': 'another'}, {'message_revision': 'other'},
    {'message_order': 3}, {'offset_start': -1}, {'text': 'x' * 1001},
])
def test_continuation_never_crosses_identity_boundaries_or_truncates_paragraph(change):
    first, second = sources()
    assert heading_continuation(first, {**second, **change}) is None


def test_plain_sources_keep_the_previous_window_shape_and_fingerprint():
    first, second = sources()
    first['text'] = first['text'].replace('4. Keep the ligand during protonation assessment', 'A complete sentence.')
    window = build_windows(bundle([first, second]), max_chars=3000)[0]
    payload = {'sources': [first], 'primary_ids': [first['id']], 'context_ids': []}
    expected = hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True,
                                        separators=(',', ':')).encode()).hexdigest()
    assert window == {**payload, 'fingerprint': expected}


def test_budget_counts_full_fragment_after_a_prefix_view_and_keeps_longest_view():
    refs = sources()
    windows = build_windows(bundle(refs), max_chars=3000)
    first_cost = sum(len(source['text']) for source in windows[0]['sources'])
    selected, _, selection = select_profile_windows(bundle(refs), windows,
        replace(get_generation_profile('economy'), source_budget_chars=first_cost))
    assert len(selected) == 1 and selection['selected_source_chars'] == first_cost
    selected, _, selection = select_profile_windows(bundle(refs), windows,
        replace(get_generation_profile('economy'), source_budget_chars=sum(len(s['text']) for s in refs)))
    assert len(selected) == 2
    assert selection['selected_source_chars'] == sum(len(s['text']) for s in refs)


def test_legacy_heading_only_interpretation_is_preserved_but_needs_context_review():
    refs = sources()
    old = {'id': 'knowledge:old', 'statement': 'Keep the ligand during protonation assessment.',
           'category': 'PROPOSAL', 'design_status': 'PROPOSED', 'scope': 'Protonation environment',
           'extraction_version': 'v1', 'evidence': [
               {'source_id': refs[0]['id'], 'role': 'assistant', 'revision': refs[0]['revision'],
                'excerpt': '4. Keep the ligand during protonation assessment'}]}
    before = deepcopy(old)
    result = k.reconcile({'items': [old]}, [], bundle(refs), 'v1')[0]
    assert result['reconciliation_state'] == 'unresolved'
    assert 'holo complex' in result['reconciliation_note']
    assert result['statement'] == old['statement'] and result['design_status'] == 'PROPOSED'
    assert result['evidence'] == old['evidence'] and old == before
    qualified = deepcopy(old)
    qualified['evidence'].append({'source_id': refs[1]['id'], 'role': 'assistant',
        'revision': refs[1]['revision'], 'excerpt': refs[1]['text'].split('\n\n')[0]})
    assert 'reconciliation_state' not in k.reconcile({'items': [qualified]}, [], bundle(refs), 'v1')[0]


def test_context_changes_invalidate_only_affected_extraction_cache(tmp_path):
    from sqlalchemy.orm import Session
    from backend.app.db import initialize
    from backend.app.models import Project

    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='Context regression'))
        db.commit()
    class Manager:
        calls = 0
        prompts = []
        def generate_brief(self, prompt, schema):
            self.calls += 1
            self.prompts.append(prompt)
            return '{"items":[]}'
    manager = Manager()
    config = k.extraction_configuration(manager)
    refs = sources()
    batch = build_windows(bundle(refs), max_chars=3000)[0]
    _, hit = k.extract_batch(engine, 1, manager, batch, config)
    assert not hit and manager.calls == 1
    assert 'A trailing heading is incomplete evidence' in manager.prompts[-1]
    _, hit = k.extract_batch(engine, 1, manager, batch, config)
    assert hit and manager.calls == 1
    revised = deepcopy(refs)
    revised[1]['text'] = revised[1]['text'].replace('generally yes', 'sometimes yes')
    revised[1]['revision'] = hashlib.sha256(revised[1]['text'].encode()).hexdigest()
    changed_batch = build_windows(bundle(revised), max_chars=3000)[0]
    _, hit = k.extract_batch(engine, 1, manager, changed_batch, config)
    assert not hit and manager.calls == 2
    # A completed ordinary source is not sent new instruction/metadata, preserving
    # its extraction key despite this policy being enabled elsewhere.
    plain = {'sources': [refs[1]], 'primary_ids': [refs[1]['id']], 'context_ids': []}
    _, hit = k.extract_batch(engine, 1, manager, plain, config)
    assert not hit and manager.calls == 3
    assert 'context_view' not in manager.prompts[-1]
    _, hit = k.extract_batch(engine, 1, manager, plain, config)
    assert hit and manager.calls == 3
    engine.dispose()


def test_capacity_can_omit_forward_view_but_heading_only_claim_still_requires_review():
    refs = sources()
    windows = build_windows(bundle(refs), max_chars=2100)
    assert len(windows[0]['sources']) == 1
    assert all(len(json.dumps(window, ensure_ascii=False)) <= 2100 for window in windows)
    old = {'id': 'knowledge:old', 'statement': 'Keep the ligand.', 'category': 'PROPOSAL',
           'design_status': 'PROPOSED', 'scope': 'Protonation', 'extraction_version': 'v1',
           'evidence': [{'source_id': refs[0]['id'], 'role': 'assistant',
                        'revision': refs[0]['revision'],
                        'excerpt': '4. Keep the ligand during protonation assessment'}]}
    result = k.reconcile({'items': [old]}, [], bundle(refs), 'v1')[0]
    assert result['reconciliation_state'] == 'unresolved'
    assert result['design_status'] == 'PROPOSED' and 'holo complex' in result['reconciliation_note']
