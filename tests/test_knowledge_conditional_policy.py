"""Exact policy context and version isolation; mocked output is not a recall eval."""
from copy import deepcopy
import hashlib
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import knowledge
from backend.app.models import Project, ProjectHistoryBatch


@pytest.fixture
def conditional_policy(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='Conditional policy fixture'))
        db.commit()
    texts = [
        ('user', '3. Use persistent evaluation records. One logical evaluation per molecule, with transitions such as:\n\n'
         '   RESERVED → STARTED → SUCCESS | FAILED\nRESERVED → CANCELLED\n\n'
         '   Success and terminal failure consume one slot. Retries reuse it. Mark STARTED before preparation/protonation begins, '
         'so those terminal failures count too. Uncertain interruptions retain capacity.\n'
         '4. Choose a narrow safe point initially. Allow reseeding only in a STARTED iteration, before candidate submission or docking. '
         'The introduced seed occupies one batch slot. It can be evaluated alone or alongside generated candidates, '
         'but becomes an eligible parent only after successful docking.\n'
         '5. Cancellation releases evaluation capacity only. An accepted seed remains in the database, '
         'and its introduction still counts toward lifetime allowance and event quota. Otherwise cancellation could bypass those limits.'),
        ('assistant', 'Cancellation releases an unused evaluation reservation but never refunds the accepted introduction quota.'),
    ]
    sources = [{'id': f'original:{index}', 'text': text, 'role': role, 'order': index,
                'revision': hashlib.sha256(text.encode()).hexdigest(),
                'conversation_id': 'original-turn', 'source_key': 'original-turn'}
               for index, (role, text) in enumerate(texts)]
    window = {'sources': sources, 'primary_ids': [sources[1]['id']], 'context_ids': [sources[0]['id']]}
    output = {'statement': 'Cancelling an unused evaluation reservation releases its capacity without refunding accepted seed introduction charges.',
              'category': 'REQUIREMENT', 'design_status': 'AGREED', 'scope': 'Reseeding cancellation',
              'rationale': '', 'limitations': '', 'alternatives': '', 'agreement_basis': 'direct_user',
              'revises_statement': '', 'conflicting_statements': [],
              'evidence': [{'source_id': source['id'], 'role': source['role'], 'excerpt': source['text']}
                           for source in sources]}

    class Capture:
        calls = 0

        def generate_extraction(self, prompt, schema):
            self.calls += 1
            self.prompt, self.schema = prompt, deepcopy(schema)
            self.payload = json.loads(prompt.split('\n', 1)[1])
            return json.dumps({'items': [output]})

    yield engine, window, output, Capture()
    engine.dispose()


def test_exact_condition_and_state_transition_context_reach_extraction_and_cache(conditional_policy):
    engine, window, expected, manager = conditional_policy
    before = deepcopy(window)
    config = knowledge.extraction_configuration(manager)
    assert config['prompt'] == 18 and config['schema'] == 3
    items, hit = knowledge.extract_batch(engine, 1, manager, window, config)
    assert not hit
    assert manager.payload['primary_ids'] == window['primary_ids']
    assert [source['text'] for source in manager.payload['sources']] == [source['text'] for source in window['sources']]
    expected_schema = deepcopy(knowledge.OUTPUT_SCHEMA)
    expected_schema['properties']['items']['items']['properties']['evidence']['items']['properties']['source_id']['enum'] = [
        source['id'] for source in window['sources']]
    assert manager.schema == expected_schema
    assert items[0]['statement'] == expected['statement']
    assert items[0]['limitations'] == ''
    assert [ref['excerpt'] for ref in items[0]['evidence']] == [source['text'] for source in window['sources']]
    replay, hit = knowledge.extract_batch(engine, 1, manager, window, config)
    assert hit and replay == items and manager.calls == 1
    assert window == before


@pytest.mark.parametrize('historical_prompt', [14, 15, 16])
def test_v17_does_not_reuse_or_rewrite_earlier_interpretation(conditional_policy, historical_prompt):
    engine, window, output, manager = conditional_policy
    config = knowledge.extraction_configuration(manager)
    historical = {**config, 'prompt': historical_prompt}
    corrected = output['statement']
    # Simulate the inspected earlier raw interpretation. Exact quotes alone do not
    # establish semantic fidelity; validation must not pretend to repair it.
    output['statement'] = 'Cancellation releases evaluation capacity but preserves accepted seed introduction charges.'
    old_items, hit = knowledge.extract_batch(engine, 1, manager, window, historical)
    assert not hit
    with Session(engine) as db:
        old_rows = {row.fingerprint: deepcopy(row.evidence) for row in db.scalars(select(ProjectHistoryBatch))}
    output['statement'] = corrected
    new_items, hit = knowledge.extract_batch(engine, 1, manager, window, config)
    assert not hit and manager.calls == 2
    assert new_items[0]['extraction_version'] != old_items[0]['extraction_version']
    assert new_items[0]['statement'] == corrected
    assert old_items[0]['statement'] != corrected
    for settings, expected in [(historical, old_items), (config, new_items)]:
        replay, hit = knowledge.extract_batch(engine, 1, manager, window, settings)
        assert hit and replay == expected and manager.calls == 2
    with Session(engine) as db:
        assert len(db.scalars(select(ProjectHistoryBatch)).all()) == 2
        for fingerprint, raw in old_rows.items():
            assert db.get(ProjectHistoryBatch, (1, fingerprint)).evidence == raw
