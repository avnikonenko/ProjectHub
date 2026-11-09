"""Structured extraction tests use original-text fixtures and mocked Codex only."""
from copy import deepcopy
import hashlib
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, ProjectHistoryBatch, ProjectKnowledge
from backend.app.integrations.codex import knowledge as k
from backend.app.integrations.codex.protocol import CodexError


def source(identifier, text, role='user', order=1, conversation='conversation:1'):
    return {'id': identifier, 'role': role, 'text': text,
            'revision': hashlib.sha256(text.encode()).hexdigest(), 'order': order,
            'conversation_id': conversation, 'source_key': conversation, 'label': identifier,
            'timestamp': None}


def item(sources=None, **changes):
    refs = sources or [source('user:1', 'Use Gemmi for ingestion.')]
    value = {'statement': 'Use Gemmi for ingestion.', 'category': 'DECISION',
             'design_status': 'AGREED', 'scope': 'project:ingestion', 'rationale': 'Preserve metadata.',
             'alternatives': 'MDAnalysis-only ingestion.', 'limitations': 'Conversion complexity.',
             'agreement_basis': 'direct_user', 'revises_statement': '', 'conflicting_statements': [],
             'evidence': [{'source_id': ref['id'], 'role': ref['role'], 'excerpt': ref['text']} for ref in refs]}
    return {**value, **changes}


def window(sources=None):
    refs = sources or [source('user:1', 'Use Gemmi for ingestion.')]
    return {'sources': refs, 'primary_ids': [refs[-1]['id']], 'context_ids': [ref['id'] for ref in refs[:-1]]}


def bundle(sources=None, availability='available'):
    refs = sources if sources is not None else window()['sources']
    return {'project': {'id': 1}, 'sources': refs,
            'manifest': {'fingerprint': 'manifest', 'entries': [{'source_key': 'conversation:1', 'availability': availability}]},
            'coverage': {'processed': ['conversation:1'], 'unavailable': [], 'excluded': [], 'unprocessed': [], 'limitations': []}}


def validate(value=None, sources=None, version='v1'):
    return k.validate_extraction(json.dumps({'items': [value or item()]}), window(sources), 1, version)[0]


def engine(tmp_path):
    result, _ = initialize(tmp_path)
    with Session(result) as db:
        db.add_all([Project(name='One'), Project(name='Two')]); db.commit()
    return result


def test_user_instruction_and_paired_acceptance_preserve_reasons():
    decision = validate()
    assert decision['design_status'] == 'AGREED'
    assert decision['rationale'] == 'Preserve metadata.'
    assert decision['evidence'][0]['revision'] == window()['sources'][0]['revision']
    refs = [source('proposal', 'Use Gemmi to preserve metadata.', 'assistant', 0), source('acceptance', 'Yes.', order=1)]
    decision = validate(item(refs, agreement_basis='proposal_acceptance'), refs)
    assert len(decision['evidence']) == 2
    assert decision['agreement_basis'] == 'proposal_acceptance'


@pytest.mark.parametrize('change,match', [
    (lambda v: v['evidence'][0].update(role='assistant'), 'speaker'),
    (lambda v: v['evidence'][0].update(excerpt='Not in the message.'), 'excerpt'),
    (lambda v: v['evidence'][0].update(source_id='foreign'), 'foreign'),
    (lambda v: v.update(agreement_basis='none'), 'user evidence'),
])
def test_bad_attribution_rejected(change, match):
    value = item(); change(value)
    with pytest.raises(CodexError, match=match): validate(value)


def test_assistant_claim_and_ambiguous_acceptance_cannot_establish_agreement():
    refs = [source('assistant:1', 'We previously agreed on Gemmi.', 'assistant')]
    with pytest.raises(CodexError, match='user evidence'): validate(item(refs), refs)
    refs = [source('user:1', 'I agree.')]
    with pytest.raises(CodexError, match='proposal context'): validate(item(refs), refs)
    with pytest.raises(CodexError, match='preceding proposal'):
        validate(item(refs, agreement_basis='proposal_acceptance'), refs)
    refs = [source('proposal', 'Use Gemmi.', 'assistant', 2, 'other'), source('acceptance', 'Yes.')]
    with pytest.raises(CodexError, match='same conversation'):
        validate(item(refs, agreement_basis='proposal_acceptance'), refs)


def test_cache_reuses_validated_extraction_and_invalidates_configuration(tmp_path):
    db_engine = engine(tmp_path)
    class Manager:
        calls = 0
        def generate_brief(self, prompt, schema):
            self.calls += 1
            assert 'unambiguous proposal' in prompt
            return json.dumps({'items': [item()]})
    manager = Manager(); config = k.extraction_configuration(manager)
    first, hit = k.extract_batch(db_engine, 1, manager, window(), config)
    assert not hit
    again, hit = k.extract_batch(db_engine, 1, manager, window(), config)
    assert hit and manager.calls == 1 and first == again
    for name in ('model', 'effort', 'account', 'schema', 'prompt', 'instructions'):
        k.extract_batch(db_engine, 1, manager, window(), {**config, name: 'changed'})
    assert manager.calls == 7
    k.extract_batch(db_engine, 2, manager, window(), config)
    assert manager.calls == 8
    db_engine.dispose()


def test_interrupted_extraction_keeps_successful_cache_not_published_knowledge(tmp_path):
    db_engine = engine(tmp_path)
    class Manager:
        calls = 0
        fail = False
        def generate_brief(self, prompt, schema):
            self.calls += 1
            if self.fail: raise CodexError('Unavailable')
            return json.dumps({'items': [item()]})
    manager = Manager(); config = k.extraction_configuration(manager)
    k.extract_batch(db_engine, 1, manager, window(), config)
    manager.fail = True
    with pytest.raises(CodexError, match='Unavailable'):
        k.extract_batch(db_engine, 1, manager, window(), {**config, 'model': 'other'})
    assert k.extract_batch(db_engine, 1, manager, window(), config)[1]
    with Session(db_engine) as db:
        assert len(list(db.scalars(select(ProjectHistoryBatch)))) == 1
        assert k.read_knowledge(db, 1) is None and k.read_knowledge(db, 2) is None
    db_engine.dispose()


def test_new_messages_preserve_older_agreement_and_rationale_without_merging_scopes():
    old = validate()
    tentative = validate(item(statement='Use Gemmi for ingestion.', category='PROPOSAL', design_status='PROPOSED',
                              scope='release:v1', agreement_basis='none'))
    reconciled = k.reconcile({'items': [old]}, [tentative], bundle(), 'v1')
    accepted = next(value for value in reconciled if value['design_status'] == 'AGREED')
    assert accepted['source_state'] == 'valid' and accepted['rationale'] == old['rationale']
    assert len(reconciled) == 2


@pytest.mark.parametrize('availability,expected', [('unavailable', 'unavailable'), ('excluded', 'excluded'), ('available', 'stale')])
def test_historical_agreement_survives_source_unavailability(availability, expected):
    old = validate()
    updated = k.reconcile({'items': [old]}, [], bundle([], availability), 'v1')[0]
    assert updated['design_status'] == 'AGREED'
    assert updated['source_state'] == expected
    assert updated['evidence'] == old['evidence']


def test_modified_context_invalidates_interpretation_without_changing_history():
    refs = [source('proposal', 'Use Gemmi.', 'assistant', 0), source('acceptance', 'Yes.', order=1)]
    old = validate(item(refs, agreement_basis='proposal_acceptance'), refs)
    changed = [source('proposal', 'Use another library.', 'assistant', 0), refs[1]]
    updated = k.reconcile({'items': [old]}, [], bundle(changed), 'v1')[0]
    assert updated['source_state'] == 'stale' and updated['design_status'] == 'AGREED'
    assert k.reconcile({'items': [old]}, [], bundle(refs), 'new-config')[0]['source_state'] == 'stale'


def test_exact_duplicates_aggregate_evidence_but_semantic_variants_stay_separate():
    original = validate()
    duplicate = deepcopy(original); duplicate['rationale'] = 'Preserve connectivity.'
    paraphrase = validate(item(statement='Gemmi should be used to ingest structures.'))
    values = k.reconcile(None, [original, duplicate, paraphrase], bundle(), 'v1')
    assert len(values) == 2
    assert all(reason in values[0]['rationale'] for reason in ('metadata', 'connectivity'))


def test_scoped_chronological_supersession_and_uncertain_conflicts():
    old = validate()
    ref = source('user:2', 'Replace Gemmi with another parser.', order=2)
    new = validate(item([ref], statement='Use another parser.', revises_statement=old['statement'],
                        conflicting_statements=[old['statement']]), [ref])
    values = k.reconcile({'items': [old]}, [new], bundle([*window()['sources'], ref]), 'v1')
    current = next(value for value in values if value['id'] == new['id'])
    assert current['supersedes'] == [old['id']]
    assert current['conflicts_with'] == [old['id']]
    assert next(value for value in values if value['id'] == old['id'])['design_status'] == 'AGREED'
    ref2 = source('user:3', ref['text'], order=3, conversation='other')
    cross = validate(item([ref2], statement='Use another parser.', revises_statement=old['statement']), [ref2])
    assert not k.reconcile({'items': [old]}, [cross], bundle([*window()['sources'], ref2]), 'v1')[-1]['supersedes']


def test_candidate_reuses_cache_without_publishing_and_checks_project(tmp_path):
    db_engine = engine(tmp_path)
    class Manager:
        calls = 0
        def generate_brief(self, prompt, schema):
            self.calls += 1
            return json.dumps({'items': [item()]})
    manager = Manager()
    first = k.build_candidate(db_engine, 1, manager, bundle())
    assert first['project_id'] == 1 and first['items'][0]['source_state'] == 'valid'
    assert first['sources'][0]['text'] == 'Use Gemmi for ingestion.'
    second = k.build_candidate(db_engine, 1, manager, bundle(), first)
    assert second['diagnostics']['cache_hits'] == 1 and manager.calls == 1
    with Session(db_engine) as db: assert k.read_knowledge(db, 1) is None
    with pytest.raises(CodexError, match='belong'):
        k.build_candidate(db_engine, 2, manager, bundle())
    db_engine.dispose()


def test_chronology_requires_later_than_old_acceptance_not_only_old_proposal():
    refs = [source('proposal', 'Use Gemmi.', 'assistant', 0), source('acceptance', 'Yes.', order=10)]
    old = validate(item(refs, agreement_basis='proposal_acceptance'), refs)
    later_than_proposal_only = source('revision', 'Replace Gemmi with another parser.', order=5)
    new = validate(item([later_than_proposal_only], statement='Use another parser.', revises_statement=old['statement']),
                   [later_than_proposal_only])
    assert not k.reconcile({'items': [old]}, [new], bundle([*refs, later_than_proposal_only]), 'v1')[-1]['supersedes']


def test_changed_source_never_attaches_old_quote_to_new_revision(tmp_path):
    db_engine = engine(tmp_path)
    old_source = source('user:1', 'Use Gemmi for ingestion.')
    manager = type('Manager', (), {'generate_brief': lambda *a: json.dumps({'items': []})})()
    old = validate(version=k._digest(k.extraction_configuration(manager)))
    previous = {'project_id': 1, 'items': [old], 'sources': [old_source]}
    current = source('user:1', 'Gemmi is no longer selected.')
    candidate = k.build_candidate(db_engine, 1, manager, bundle([current]), previous)
    assert candidate['items'][0]['source_state'] == 'stale'
    assert candidate['items'][0]['evidence'][0]['excerpt'] == old_source['text']
    assert candidate['sources'] == []
    db_engine.dispose()


def test_fresh_equivalent_does_not_discard_previous_valid_rationale_or_repeat_it():
    old = validate()
    fresh = validate(item(rationale='', alternatives='', limitations=''))
    first = k.reconcile({'items': [old]}, [fresh], bundle(), 'v1')[0]
    assert first['rationale'] == old['rationale']
    assert first['alternatives'] == old['alternatives']
    assert first['limitations'] == old['limitations']
    second = k.reconcile({'items': [first]}, [fresh], bundle(), 'v1')[0]
    assert second['rationale'] == first['rationale']


def test_duplicate_support_unions_context_dependencies():
    old = validate()
    extra = source('context:2', 'Additional context.', 'assistant', 2)
    newer = deepcopy(old)
    newer['context_dependencies'].append({'source_id': extra['id'], 'revision': extra['revision'], 'source_key': extra['source_key']})
    first = k.reconcile(None, [old, newer], bundle([*window()['sources'], extra]), 'v1')[0]
    assert len(first['context_dependencies']) == 2
    changed = source('context:2', 'Changed context.', 'assistant', 2)
    second = k.reconcile({'items': [first]}, [], bundle([*window()['sources'], changed]), 'v1')[0]
    assert second['source_state'] == 'stale'


def test_unavailable_supersession_does_not_resurrect_older_choice_on_cache_replay():
    old = validate()
    ref = source('user:2', 'Replace Gemmi with another parser.', order=2, conversation='conversation:1')
    replacement = validate(item([ref], statement='Use another parser.', revises_statement=old['statement']), [ref])
    published = k.reconcile({'items': [old]}, [replacement], bundle([*window()['sources'], ref]), 'v1')
    # The original support is re-extracted/cached; the later revision is absent.
    replay = k.reconcile({'items': published}, [validate()], bundle(), 'v1')
    old_again = next(value for value in replay if value['id'] == old['id'])
    assert old_again['superseded_by'] == [replacement['id']]
    assert old_again['design_status'] == 'AGREED'


def test_readoption_with_reused_claim_identity_remains_unresolved_without_cycle():
    old = validate()
    b = source('user:2', 'Replace Gemmi with another parser.', order=2)
    replacement = validate(item([b], statement='Use another parser.', revises_statement=old['statement']), [b])
    prior = k.reconcile({'items': [old]}, [replacement], bundle([*window()['sources'], b]), 'v1')
    a_again = source('user:3', 'Replace the other parser with Gemmi.', order=3)
    readopted = validate(item([a_again], revises_statement=replacement['statement']), [a_again])
    values = k.reconcile({'items': prior}, [readopted], bundle([*window()['sources'], b, a_again]), 'v1')
    assert all(value.get('reconciliation_state') == 'unresolved' for value in values)
    original = next(value for value in values if value['id'] == old['id'])
    assert replacement['id'] not in original['supersedes']
    assert any(relation['status'] == 'unresolved_identity_cycle' for relation in original['relationships'])
