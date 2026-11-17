"""Integrity and screening regressions using fictional evidence and interpretations."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import pytest


FIXTURE = Path(__file__).parent / 'fixtures/knowledge/synthetic_scientific_recall.json'


def load_recall_reference():
    return json.loads(FIXTURE.read_text())


def screen_recall_cases(snapshot, reference=None):
    """Nominate source-linked topic/status candidates for subsequent manual review.

    The legacy state describes independent-statement candidates. Presence is a
    separate axis: a policy in rationale/limitations is not quotation-only loss,
    but is not thereby an independent policy record. Detail patterns may omit a
    qualifier already pinned by the original source; statement patterns do not.
    These lexical screens cannot establish semantic equivalence, scope, or
    correct interpretation and all results require manual source review.
    Source-specific combination checks flag obvious jointly named actions; their
    absence does not prove semantic independence or exhaustive atomicity.
    Returned states must not be aggregated into an accuracy percentage.
    """
    reference = reference or load_recall_reference()
    if snapshot.get('project_id') != reference['project_id_at_annotation']:
        raise ValueError('Recall screening requires the annotated project snapshot.')
    results = []
    for case in reference['cases']:
        source_linked, exact_quote, evidence_topic, statement_topic, matching = [], [], [], [], []
        structured_details = []
        matching_status, matching_category, bundled = [], [], []
        allowed = case['expected'].get('allowed_classifications', [case['expected']])
        for index, item in enumerate(snapshot.get('items', [])):
            identity = item.get('id', f'unidentified-item-{index}')
            evidence = [ref for ref in item.get('evidence', []) if any(
                isinstance(ref.get('source_id'), str)
                and (ref['source_id'] == original['message_id']
                     or ref['source_id'].startswith(original['message_id'] + ':p:'))
                and ref.get('role') == original['role']
                for original in case['evidence'])]
            if not evidence:
                continue
            source_linked.append(identity)
            if any(original['excerpt'] in ref.get('excerpt', '') for original in case['evidence'] for ref in evidence):
                exact_quote.append(identity)
            quote_text = '\n'.join(ref.get('excerpt', '') for ref in evidence)
            if all(re.search(pattern, quote_text, re.I) for pattern in case['topic_patterns']):
                evidence_topic.append(identity)
            # Detail wording may abbreviate an evidenced qualifier, but an
            # unrelated passage from the same long message is not support.
            detail_supported = identity in evidence_topic or identity in exact_quote
            for field in ('rationale', 'limitations', 'alternatives'):
                detail = item.get(field)
                patterns = case.get('structured_detail_patterns', case['topic_patterns'])
                if (detail_supported and isinstance(detail, str) and detail.strip()
                        and all(re.search(pattern, detail, re.I) for pattern in patterns)):
                    structured_details.append({
                        'item_id': identity, 'field': field,
                        'classification_matches_reference': any(
                            item.get('design_status') == value['design_status']
                            and item.get('category') == value['category'] for value in allowed),
                    })
            if all(re.search(pattern, item.get('statement', ''), re.I) for pattern in case['topic_patterns']):
                statement_topic.append(identity)
                if any(all(re.search(pattern, item.get('statement', ''), re.I)
                           for pattern in check['all_patterns'])
                       for check in case.get('independent_claim_checks', [])):
                    bundled.append(identity)
                if item.get('design_status') in {value['design_status'] for value in allowed}:
                    matching_status.append(identity)
                if item.get('category') in {value['category'] for value in allowed}:
                    matching_category.append(identity)
                if any(item.get('design_status') == value['design_status']
                       and item.get('category') == value['category'] for value in allowed):
                    matching.append(identity)
        independent = [identity for identity in matching if identity not in bundled]
        detail_ids = {candidate['item_id'] for candidate in structured_details}
        quoted_ids = set(evidence_topic) | set(exact_quote)
        quotation_only = [identity for identity in source_linked
                          if identity in quoted_ids and identity not in detail_ids
                          and identity not in statement_topic]
        presence = ('statement_candidate_requires_review' if statement_topic else
                    'structured_detail_candidate_requires_review' if structured_details else
                    'quotation_only_candidate_requires_review' if quotation_only else
                    'not_found_in_selected_extraction')
        state = ('candidate_match_requires_semantic_review' if independent else
                 'combined_material_claim_requires_review' if bundled else
                 'topic_status_requires_review' if statement_topic else
                 'evidence_only' if evidence_topic or exact_quote else 'not_found_in_selected_extraction')
        results.append({'case_id': case['id'], 'state': state,
                        'presence_state': presence,
                        'structured_detail_candidates': structured_details,
                        'quotation_only_item_ids': quotation_only,
                        'manual_review_required': True,
                        'expected_status': case['expected']['design_status'],
                        'expected_category': case['expected']['category'],
                        'allowed_classifications': allowed,
                        'source_linked_item_ids': source_linked,
                        'exact_excerpt_item_ids': exact_quote,
                        'evidence_topic_item_ids': evidence_topic,
                        'statement_topic_item_ids': statement_topic,
                        'matching_status_item_ids': matching_status,
                        'matching_category_item_ids': matching_category,
                        'classification_match_item_ids': matching,
                        'bundled_candidate_ids': bundled,
                        'matching_candidate_item_ids': independent})
    return results


def test_synthetic_recall_fixture_is_source_pinned():
    reference = load_recall_reference()
    original = json.loads((FIXTURE.parent / 'synthetic_design_reference.json').read_text())
    originals = {source['id']: source for source in original['sources']}
    sources = {source['id']: source for source in reference['sources']}
    assert reference['annotation_status'] == 'synthetic_fixture'
    assert reference['contains_full_transcripts'] is False
    assert len(reference['cases']) == 7
    assert len({case['id'] for case in reference['cases']}) == 7
    for case in reference['cases']:
        assert case['annotation_status'] == reference['annotation_status']
        if case['id'] == 'initial_dataset_direct_selection':
            assert case['expected']['design_status'] == 'AGREED'
            assert case['expected']['agreement_basis'] == 'direct_user'
            assert all(evidence['role'] == 'user' for evidence in case['evidence'])
        else:
            assert case['expected']['design_status'] not in ('AGREED', 'REJECTED')
        for pattern in case['topic_patterns']:
            re.compile(pattern)
        for pattern in case.get('structured_detail_patterns', []):
            re.compile(pattern)
        for check in case.get('independent_claim_checks', []):
            for pattern in check['all_patterns']:
                re.compile(pattern)
        context = case.get('independence_context', {})
        if context:
            assert context['annotation_status'] == reference['annotation_status']
        for evidence in [*case['evidence'], *context.get('evidence', [])]:
            source = sources[evidence['source_id']]
            assert source['content_sha256'] == originals[source['id']]['content_sha256']
            assert evidence['source_revision'] == source['content_sha256']
            assert evidence['transcript_end'] - evidence['transcript_start'] == len(evidence['excerpt'])
            assert evidence['message_end'] - evidence['message_start'] == len(evidence['excerpt'])
            assert hashlib.sha256(evidence['excerpt'].encode()).hexdigest() == evidence['excerpt_sha256']
            assert evidence['role'] in ('user', 'assistant')
            assert evidence['message_id'].endswith(':m:' + str(evidence['message_ordinal']))
            assert all(identifier.startswith(evidence['message_id'] + ':p:') for identifier in evidence['fragment_ids'])


def test_source_speaker_and_explicit_stereochemistry_are_retained():
    cases = {case['id']: case for case in load_recall_reference()['cases']}
    for identifier in ('optional_mw_filter', 'tentative_component_separation', 'toolkit_suitability_concern'):
        assert cases[identifier]['evidence'][0]['role'] == 'user'
    for identifier in ('waters_as_preparation_context', 'configurable_water_retention', 'stereochemistry_inspection'):
        assert cases[identifier]['evidence'][0]['role'] == 'assistant'
    assert 'stereochemistry' in cases['stereochemistry_inspection']['evidence'][0]['excerpt']
    assert cases['toolkit_suitability_concern']['expected']['design_status'] == 'UNRESOLVED'


def _screen_item(case, statement, status='PROPOSED', category='PROPOSAL'):
    evidence = case['evidence'][0]
    return {'id': 'synthetic-record', 'statement': statement, 'design_status': status, 'category': category,
            'evidence': [{'source_id': evidence['fragment_ids'][0], 'role': evidence['role'], 'excerpt': evidence['excerpt']}]}


def test_quote_only_retention_is_distinct_from_independent_proposal():
    case = next(case for case in load_recall_reference()['cases'] if case['id'] == 'optional_mw_filter')
    snapshot = {'project_id': 2, 'items': [_screen_item(case, 'Which ligands should be retained?', 'UNRESOLVED', 'OPEN_QUESTION')]}
    before = deepcopy(snapshot)
    result = next(row for row in screen_recall_cases(snapshot) if row['case_id'] == case['id'])
    assert result['state'] == 'evidence_only'
    assert result['exact_excerpt_item_ids'] == ['synthetic-record']
    assert not result['matching_candidate_item_ids']
    assert snapshot == before
    snapshot['items'].append({**_screen_item(case, 'Consider an optional molecular-weight threshold.'), 'id': 'independent-proposal'})
    result = next(row for row in screen_recall_cases(snapshot) if row['case_id'] == case['id'])
    assert result['state'] == 'candidate_match_requires_semantic_review'
    assert result['matching_candidate_item_ids'] == ['independent-proposal']


def test_topic_hit_does_not_override_status_or_source_ownership():
    case = next(case for case in load_recall_reference()['cases'] if case['id'] == 'waters_as_preparation_context')
    proposed = _screen_item(case, 'Consider waters in preparation context.')
    result = next(row for row in screen_recall_cases({'project_id': 2, 'items': [proposed]}) if row['case_id'] == case['id'])
    assert result['state'] == 'candidate_match_requires_semantic_review'
    unsupported = {**proposed, 'design_status': 'AGREED'}
    result = next(row for row in screen_recall_cases({'project_id': 2, 'items': [unsupported]}) if row['case_id'] == case['id'])
    assert result['state'] == 'topic_status_requires_review'
    assert not result['matching_candidate_item_ids']
    foreign = deepcopy(proposed); foreign['evidence'][0]['source_id'] = 'chatgpt:note:999:m:2:p:1'
    result = next(row for row in screen_recall_cases({'project_id': 2, 'items': [foreign]}) if row['case_id'] == case['id'])
    assert result['state'] == 'not_found_in_selected_extraction'
    try:
        screen_recall_cases({'project_id': 3, 'items': [proposed]})
    except ValueError:
        pass
    else:
        raise AssertionError('Foreign project snapshot was accepted')


def test_toolkit_concern_allows_reported_uncertainty_without_forcing_question_category():
    case = next(case for case in load_recall_reference()['cases'] if case['id'] == 'toolkit_suitability_concern')
    for category, status in [('OPEN_QUESTION', 'UNRESOLVED'), ('FINDING', 'UNKNOWN')]:
        record = _screen_item(case, "The user questions Birch Toolkit's suitability.", status, category)
        result = next(row for row in screen_recall_cases({'project_id': 2, 'items': [record]}) if row['case_id'] == case['id'])
        assert result['state'] == 'candidate_match_requires_semantic_review'
        assert result['matching_status_item_ids'] == ['synthetic-record']
        assert result['matching_category_item_ids'] == ['synthetic-record']
    record = _screen_item(case, 'Reject Birch Toolkit.', 'REJECTED', 'DECISION')
    result = next(row for row in screen_recall_cases({'project_id': 2, 'items': [record]}) if row['case_id'] == case['id'])
    assert result['state'] == 'topic_status_requires_review'
    assert not result['matching_candidate_item_ids']


def test_direct_dataset_selection_remains_separate_from_nearby_tentative_ideas():
    case = next(case for case in load_recall_reference()['cases'] if case['id'] == 'initial_dataset_direct_selection')
    assert case['evidence'][0]['excerpt'].startswith('For our first dataset, ')
    record = _screen_item(case, 'Use the linked fixture as the first dataset.', 'AGREED', 'DECISION')
    record['agreement_basis'] = 'direct_user'
    result = next(row for row in screen_recall_cases({'project_id': 2, 'items': [record]}) if row['case_id'] == case['id'])
    assert result['state'] == 'candidate_match_requires_semantic_review'
    downgraded = {**record, 'design_status': 'PROPOSED', 'category': 'PROPOSAL'}
    result = next(row for row in screen_recall_cases({'project_id': 2, 'items': [downgraded]}) if row['case_id'] == case['id'])
    assert result['state'] == 'topic_status_requires_review'
    assert not result['matching_candidate_item_ids']
    tentative = {case['id']: case for case in load_recall_reference()['cases']}
    assert tentative['optional_mw_filter']['expected']['design_status'] == 'PROPOSED'
    assert tentative['tentative_component_separation']['expected']['design_status'] == 'PROPOSED'


def test_same_status_separation_and_script_reuse_do_not_count_as_independent_coverage():
    reference = load_recall_reference()
    case = next(case for case in reference['cases'] if case['id'] == 'tentative_component_separation')
    combined = _screen_item(case, 'Consider protein, small-molecule and ion separation and reuse of existing PDB scripts, including PDB information retrieval.')
    snapshot = {'project_id': 2, 'items': [combined]}
    before = deepcopy(snapshot)
    result = next(row for row in screen_recall_cases(snapshot) if row['case_id'] == case['id'])
    assert result['state'] == 'combined_material_claim_requires_review'
    assert result['matching_status_item_ids'] == ['synthetic-record']
    assert result['classification_match_item_ids'] == ['synthetic-record']
    assert result['bundled_candidate_ids'] == ['synthetic-record']
    assert not result['matching_candidate_item_ids']
    assert snapshot == before
    separate = {**_screen_item(case, 'Consider protein, small-molecule and ion separation.'), 'id': 'separation-only'}
    scripts = {**_screen_item(case, 'Consider reuse of existing PDB scripts.'), 'id': 'script-reuse-only'}
    result = next(row for row in screen_recall_cases({'project_id': 2, 'items': [separate, scripts]}) if row['case_id'] == case['id'])
    assert result['state'] == 'candidate_match_requires_semantic_review'
    assert result['matching_candidate_item_ids'] == ['separation-only']
    assert not result['bundled_candidate_ids']
    # The annotation is source-specific: an unrelated message must not be
    # declared a source-supported occurrence merely from matching keywords.
    foreign = deepcopy(combined)
    foreign['evidence'][0]['source_id'] = 'chatgpt:note:7:m:2:p:1'
    result = next(row for row in screen_recall_cases({'project_id': 2, 'items': [foreign]}) if row['case_id'] == case['id'])
    assert result['state'] == 'not_found_in_selected_extraction'
    assert not result['bundled_candidate_ids']


@pytest.mark.parametrize('statement', [
    'Retain metal-coordinating waters unless the selected protocol excludes them.',
    'Make metal retention configurable and preserve coordinating waters.',
    'Keep crystallographic waters during preparation.',
])
def test_configurable_water_screen_requires_the_independent_specific_policy(statement):
    case = next(case for case in load_recall_reference()['cases']
                if case['id'] == 'configurable_water_retention')
    # Even the exact table excerpt cannot turn a different statement into this policy.
    other = _screen_item(case, statement)
    snapshot = {'project_id': 2, 'items': [other]}
    before = deepcopy(snapshot)
    result = next(row for row in screen_recall_cases(snapshot) if row['case_id'] == case['id'])
    assert result['state'] == 'evidence_only'
    assert result['exact_excerpt_item_ids'] == ['synthetic-record']
    assert not result['statement_topic_item_ids']
    assert not result['matching_candidate_item_ids']
    assert snapshot == before

    policy = {**_screen_item(case, 'Apply a configurable retention policy to crystallographic waters.'),
              'id': 'independent-water-policy'}
    snapshot['items'].append(policy)
    result = next(row for row in screen_recall_cases(snapshot) if row['case_id'] == case['id'])
    assert result['state'] == 'candidate_match_requires_semantic_review'
    assert result['matching_candidate_item_ids'] == ['independent-water-policy']


@pytest.mark.parametrize('field', ['rationale', 'limitations', 'alternatives'])
def test_structured_water_policy_detail_is_not_quote_only_or_independent(field):
    case = next(case for case in load_recall_reference()['cases']
                if case['id'] == 'configurable_water_retention')
    record = _screen_item(case, 'Maintain a master complex with mappings.')
    record[field] = 'Make water retention configurable.'
    snapshot = {'project_id': 2, 'items': [record]}
    before = deepcopy(snapshot)
    result = next(row for row in screen_recall_cases(snapshot) if row['case_id'] == case['id'])
    # Legacy statement-level classification remains available to older callers.
    assert result['state'] == 'evidence_only'
    assert result['presence_state'] == 'structured_detail_candidate_requires_review'
    assert result['structured_detail_candidates'] == [{
        'item_id': 'synthetic-record', 'field': field, 'classification_matches_reference': True,
    }]
    assert not result['quotation_only_item_ids']
    assert not result['matching_candidate_item_ids']
    assert result['manual_review_required'] is True
    assert snapshot == before

    snapshot['items'].append({**_screen_item(case,
        'Apply a configurable retention policy to crystallographic waters.'), 'id': 'policy'})
    result = next(row for row in screen_recall_cases(snapshot) if row['case_id'] == case['id'])
    assert result['presence_state'] == 'statement_candidate_requires_review'
    assert result['matching_candidate_item_ids'] == ['policy']
    assert result['structured_detail_candidates'][0]['item_id'] == 'synthetic-record'


def test_quote_only_water_policy_and_unrelated_detail_remain_separate():
    case = next(case for case in load_recall_reference()['cases']
                if case['id'] == 'configurable_water_retention')
    record = _screen_item(case, 'Maintain a master complex with mappings.')
    record['limitations'] = 'Keep metal coordination configurable and preserve waters.'
    result = next(row for row in screen_recall_cases({'project_id': 2, 'items': [record]})
                  if row['case_id'] == case['id'])
    assert result['presence_state'] == 'quotation_only_candidate_requires_review'
    assert not result['structured_detail_candidates']
    assert result['quotation_only_item_ids'] == ['synthetic-record']


def test_detail_presence_never_overrides_status_or_original_source():
    case = next(case for case in load_recall_reference()['cases']
                if case['id'] == 'configurable_water_retention')
    record = _screen_item(case, 'Maintain a master complex.', 'AGREED', 'DECISION')
    record['limitations'] = 'Make water retention configurable.'
    result = next(row for row in screen_recall_cases({'project_id': 2, 'items': [record]})
                  if row['case_id'] == case['id'])
    assert result['presence_state'] == 'structured_detail_candidate_requires_review'
    assert not result['structured_detail_candidates'][0]['classification_matches_reference']
    assert not result['matching_candidate_item_ids']
    record['evidence'][0]['source_id'] = 'chatgpt:note:999:m:2:p:1'
    result = next(row for row in screen_recall_cases({'project_id': 2, 'items': [record]})
                  if row['case_id'] == case['id'])
    assert result['presence_state'] == 'not_found_in_selected_extraction'
    assert not result['structured_detail_candidates']

    record['evidence'][0]['source_id'] = case['evidence'][0]['fragment_ids'][0]
    record['evidence'][0]['excerpt'] = 'Use a master complex with atom mappings.'
    result = next(row for row in screen_recall_cases({'project_id': 2, 'items': [record]})
                  if row['case_id'] == case['id'])
    assert result['presence_state'] == 'not_found_in_selected_extraction'
    assert not result['structured_detail_candidates']


def test_coherent_inspection_statement_can_retain_multiple_chemical_checks():
    case = next(case for case in load_recall_reference()['cases']
                if case['id'] == 'stereochemistry_inspection')
    record = _screen_item(case,
        'Inspect connectivity, ligand bond orders and stereochemistry before repairs.')
    result = next(row for row in screen_recall_cases({'project_id': 2, 'items': [record]})
                  if row['case_id'] == case['id'])
    assert result['presence_state'] == 'statement_candidate_requires_review'
    assert result['matching_candidate_item_ids'] == ['synthetic-record']
    assert not result['bundled_candidate_ids']
    assert result['manual_review_required'] is True
