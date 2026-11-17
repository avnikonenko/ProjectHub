"""Field fidelity and cache versioning with mocked output, not semantic accuracy."""
from copy import deepcopy
import hashlib
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, ProjectHistoryBatch, ProjectKnowledge
from backend.app.integrations.codex import knowledge
from backend.app.integrations.codex.protocol import CodexError


@pytest.fixture
def extraction(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='Field support evaluation')); db.commit()
    texts = [
        ('user', 'Use archive record R17 for the initial benchmark.'),
        ('assistant', 'Make retention configurable independently of export format. '
         'This supports separate application needs. Offer backend A as optional; '
         'backend A cannot read format B. Validate record identity and ordering before conversion.'),
    ]
    sources = [{'id': f'original:{index}', 'text': text, 'role': role,
                'revision': hashlib.sha256(text.encode()).hexdigest(), 'order': index,
                'conversation_id': 'conversation', 'source_key': 'conversation', 'label': str(index)}
               for index, (role, text) in enumerate(texts)]
    window = {'sources': sources, 'primary_ids': [s['id'] for s in sources], 'context_ids': []}

    def item(statement, source, quote, **changes):
        return {'statement': statement, 'category': 'PROPOSAL', 'design_status': 'PROPOSED',
                'scope': 'project', 'rationale': '', 'limitations': '', 'alternatives': '',
                'agreement_basis': 'none', 'revises_statement': '', 'conflicting_statements': [],
                'evidence': [{'source_id': source['id'], 'role': source['role'], 'excerpt': quote}],
                **changes}

    items = [
        item(texts[0][1], sources[0], texts[0][1], category='DECISION', design_status='AGREED',
             agreement_basis='direct_user', scope='Initial dataset'),
        item('Make retention configurable independently of export format.', sources[1],
             'Make retention configurable independently of export format. This supports separate application needs.',
             scope='Retention policy', rationale='Support separate application needs.'),
        item('Offer backend A as optional.', sources[1],
             'Offer backend A as optional; backend A cannot read format B.',
             scope='Backend selection', limitations='Backend A cannot read format B.'),
        item('Validate record identity and ordering before conversion.', sources[1],
             'Validate record identity and ordering before conversion.',
             category='REQUIREMENT', scope='Conversion integrity'),
    ]

    class Manager:
        calls = 0
        failure = None

        def generate_extraction(self, prompt, schema):
            self.calls += 1
            if self.failure:
                raise self.failure
            return json.dumps({'items': items})

    yield engine, window, items, Manager()
    engine.dispose()


@pytest.mark.parametrize('historical_schema', [knowledge.SCHEMA_VERSION - 1, knowledge.SCHEMA_VERSION])
def test_changed_extraction_instructions_use_new_cache_and_retain_previous_batches(extraction, historical_schema):
    engine, window, _, manager = extraction
    current = knowledge.extraction_configuration(manager)
    previous_configs = [{**current, 'schema': historical_schema, 'prompt': current['prompt'] - delta}
                        for delta in (2, 1)]
    old_results = []
    for previous in previous_configs:
        old_items, hit = knowledge.extract_batch(engine, 1, manager, window, previous)
        assert hit is False
        old_results.append(old_items)
    with Session(engine) as db:
        old_evidence = {row.fingerprint: deepcopy(row.evidence)
                        for row in db.scalars(select(ProjectHistoryBatch))}
    new_items, hit = knowledge.extract_batch(engine, 1, manager, window, current)
    assert hit is False and manager.calls == 3
    assert len({items[0]['extraction_version'] for items in [*old_results, new_items]}) == 3
    for previous, old_items in zip(previous_configs, old_results):
        assert [item['id'] for item in old_items] == [item['id'] for item in new_items]
        replay, hit = knowledge.extract_batch(engine, 1, manager, window, previous)
        assert hit and replay == old_items and manager.calls == 3
    with Session(engine) as db:
        assert len(db.scalars(select(ProjectHistoryBatch)).all()) == 3
        for fingerprint, original in old_evidence.items():
            assert db.get(ProjectHistoryBatch, (1, fingerprint)).evidence == original


def test_failed_new_instruction_batch_preserves_old_cache_and_published_state(extraction):
    engine, window, _, manager = extraction
    current = knowledge.extraction_configuration(manager)
    previous = {**current, 'prompt': current['prompt'] - 1}
    old_items, _ = knowledge.extract_batch(engine, 1, manager, window, previous)
    manager.failure = CodexError('Model output unavailable')
    with pytest.raises(CodexError, match='Model output unavailable'):
        knowledge.extract_batch(engine, 1, manager, window, current)
    replay, hit = knowledge.extract_batch(engine, 1, manager, window, previous)
    assert hit and replay == old_items and manager.calls == 2
    with Session(engine) as db:
        assert len(db.scalars(select(ProjectHistoryBatch)).all()) == 1
        assert db.get(ProjectKnowledge, 1) is None


def test_schema_change_alone_invalidates_reuse_without_discarding_the_previous_cache(extraction):
    engine, window, _, manager = extraction
    current = knowledge.extraction_configuration(manager)
    previous = {**current, 'schema': current['schema'] - 1}
    assert previous['prompt'] == current['prompt']
    old_items, hit = knowledge.extract_batch(engine, 1, manager, window, previous)
    assert not hit
    new_items, hit = knowledge.extract_batch(engine, 1, manager, window, current)
    assert not hit and manager.calls == 2
    assert [item['id'] for item in new_items] == [item['id'] for item in old_items]
    assert new_items[0]['extraction_version'] != old_items[0]['extraction_version']
    replay, hit = knowledge.extract_batch(engine, 1, manager, window, previous)
    assert hit and replay == old_items and manager.calls == 2
    with Session(engine) as db:
        assert len(db.scalars(select(ProjectHistoryBatch)).all()) == 2


def test_supported_policy_invariant_and_caveat_fields_survive_cache_replay(extraction):
    engine, window, expected, manager = extraction
    config = knowledge.extraction_configuration(manager)
    items, _ = knowledge.extract_batch(engine, 1, manager, window, config)
    fields = ('statement', 'category', 'design_status', 'scope', 'rationale', 'limitations',
              'alternatives', 'agreement_basis')
    assert [{key: i[key] for key in fields} for i in items] == [
        {key: i[key] for key in fields} for i in expected]
    assert items[0]['limitations'] == ''
    for item in items:
        for reference in item['evidence']:
            source = next(s for s in window['sources'] if s['id'] == reference['source_id'])
            assert reference['role'] == source['role']
            assert reference['excerpt'] in source['text']
            assert reference['revision'] == source['revision']
    replay, hit = knowledge.extract_batch(engine, 1, manager, window, config)
    assert hit and replay == items and manager.calls == 1


def test_new_instruction_replacement_does_not_restore_an_old_unsupported_limitation(extraction):
    engine, window, output, manager = extraction
    current = knowledge.extraction_configuration(manager)
    previous_config = {**current, 'prompt': current['prompt'] - 1}
    # Deliberately model a historical interpretation error. Exact citation
    # validation alone cannot establish whether this optional claim is true.
    output[0]['limitations'] = 'This version is not pinned.'
    old_items, _ = knowledge.extract_batch(engine, 1, manager, window, previous_config)
    previous = {'project_id': 1, 'items': old_items}
    untouched = deepcopy(previous)
    output[0]['limitations'] = ''
    fresh_items, _ = knowledge.extract_batch(engine, 1, manager, window, current)
    assert fresh_items[0]['id'] == old_items[0]['id']
    bundle = {'sources': window['sources'], 'manifest': {
        'entries': [{'source_key': 'conversation', 'availability': 'available'}]}}
    reconciled = knowledge.reconcile(previous, fresh_items[:1], bundle,
                                     fresh_items[0]['extraction_version'])
    by_id = {item['id']: item for item in reconciled}
    replacement = by_id[old_items[0]['id']]
    assert replacement['limitations'] == ''
    assert replacement.get('limitations_variants', []) == []
    assert replacement['source_state'] == 'valid'
    assert replacement['extraction_version'] == fresh_items[0]['extraction_version']
    assert all(by_id[item['id']]['source_state'] == 'stale' for item in old_items[1:])
    assert previous == untouched
    old_replay, hit = knowledge.extract_batch(engine, 1, manager, window, previous_config)
    assert hit and old_replay == old_items and manager.calls == 2
