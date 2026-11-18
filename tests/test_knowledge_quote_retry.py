"""Fresh quote-format fallback is bounded; canonical cache validation stays strict."""
from copy import deepcopy
import hashlib
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import knowledge, progress
from backend.app.integrations.codex.protocol import CodexError, JSONFormattingError
from backend.app.models import Project, ProjectHistoryBatch


@pytest.fixture
def quote_batch(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='Quote retry fixture'))
        db.commit()
    literal = 'Use `change = before - after`; preserve the original expression.'
    sources = [{'id': identifier, 'role': 'assistant', 'text': text,
                'revision': hashlib.sha256(text.encode()).hexdigest(),
                'conversation_id': 'conversation', 'source_key': 'conversation', 'order': index}
               for index, (identifier, text) in enumerate([
                   ('source:1', literal), ('source:2', 'Keep the independent second observation.')])]
    window = {'sources': sources, 'primary_ids': ['source:1', 'source:2'], 'context_ids': []}
    item = {'statement': 'Use the stated expression.', 'category': 'PROPOSAL',
            'design_status': 'PROPOSED', 'scope': 'Formula', 'rationale': '', 'alternatives': '',
            'limitations': '', 'agreement_basis': 'none', 'revises_statement': '',
            'conflicting_statements': [],
            'evidence': [{'source_id': 'source:1', 'role': 'assistant', 'excerpt': literal}]}
    yield engine, window, item
    engine.dispose()


class Responses:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_extraction(self, prompt, schema):
        self.calls.append((prompt, deepcopy(schema)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(prompt, schema)
        return response if isinstance(response, str) else json.dumps(response)


def response(item):
    return {'items': [deepcopy(item)]}


def corrupted(item):
    item = deepcopy(item)
    item['evidence'][0]['excerpt'] = 'Use `change = after - before`; preserve the original expression.'
    return response(item)


def selected(item, *, bad_selector=False, wrong_owner=False, role_override=None):
    def result(prompt, schema):
        payload = json.loads(prompt.rsplit('\n', 1)[1])
        assert all('text' not in source and source['literal_quotes'] for source in payload['sources'])
        quote = payload['sources'][0]['literal_quotes'][0]
        assert quote['text'] == item['evidence'][0]['excerpt']
        assert quote['id'] in schema['properties']['items']['items']['properties']['evidence']['items']['properties']['excerpt']['enum']
        value = deepcopy(item)
        ref = value['evidence'][0]
        ref.pop('source_id')
        ref['excerpt'] = 'unknown-selector' if bad_selector else quote['id']
        if wrong_owner:
            ref['source_id'] = payload['sources'][1]['id']
        if role_override is not None:
            ref['role'] = role_override
        return response(value)
    return result


def rows(engine):
    with Session(engine) as db:
        return [(row.fingerprint, deepcopy(row.evidence))
                for row in db.scalars(select(ProjectHistoryBatch))]


def test_successful_first_pass_keeps_original_schema_and_one_call(quote_batch):
    engine, window, item = quote_batch
    manager = Responses([response(item)])
    configuration = knowledge.extraction_configuration(manager)
    before = deepcopy(window)
    items, hit = knowledge.extract_batch(engine, 1, manager, window, configuration)
    assert not hit and len(manager.calls) == 1
    prompt, schema = manager.calls[0]
    payload = json.loads(prompt.rsplit('\n', 1)[1])
    assert [source['text'] for source in payload['sources']] == [source['text'] for source in window['sources']]
    expected = deepcopy(knowledge.OUTPUT_SCHEMA)
    expected['properties']['items']['items']['properties']['evidence']['items']['properties']['source_id']['enum'] = ['source:1', 'source:2']
    assert schema == expected
    assert items[0]['evidence'][0]['excerpt'] == item['evidence'][0]['excerpt']
    assert json.loads(rows(engine)[0][1][0]['raw']) == response(item)
    assert window == before


def test_fresh_copy_error_retries_once_and_caches_original_literals(quote_batch, monkeypatch):
    engine, window, item = quote_batch
    manager = Responses([corrupted(item), selected(item)])
    configuration = knowledge.extraction_configuration(manager)
    before = deepcopy(window)
    items, hit = knowledge.extract_batch(engine, 1, manager, window, configuration)
    assert not hit and len(manager.calls) == 2
    assert items[0]['evidence'][0]['excerpt'] == window['sources'][0]['text']
    envelope = rows(engine)[0][1][0]
    assert envelope['quote_selection_mode']
    assert json.loads(envelope['raw']) == response(item)
    assert 'after - before' not in envelope['raw']
    assert window == before

    def exact_only(quote, text):
        if quote not in text:
            raise CodexError('Old literal validator rejected a nonliteral quote.')
        return quote

    monkeypatch.setattr(knowledge, '_source_excerpt', exact_only)
    replay, hit = knowledge.extract_batch(engine, 1, manager, window, configuration)
    assert hit and replay == items and len(manager.calls) == 2


def test_fresh_speaker_mismatch_retries_once_and_caches_verified_speaker(quote_batch):
    engine, window, item = quote_batch
    mismatched = response(item)
    mismatched['items'][0]['evidence'][0]['role'] = 'user'
    manager = Responses([mismatched, selected(item)])
    configuration = knowledge.extraction_configuration(manager)

    items, hit = knowledge.extract_batch(engine, 1, manager, window, configuration)

    assert not hit and len(manager.calls) == 2
    assert items[0]['evidence'][0]['role'] == 'assistant'
    assert json.loads(rows(engine)[0][1][0]['raw']) == response(item)
    replay, hit = knowledge.extract_batch(engine, 1, manager, window, configuration)
    assert hit and replay == items and len(manager.calls) == 2


@pytest.mark.parametrize('failure', ['selector', 'source_owner', 'invalid_role', 'invalid_json'])
def test_second_failure_is_bounded_and_never_cached(quote_batch, failure):
    engine, window, item = quote_batch
    second = ('not JSON' if failure == 'invalid_json' else selected(
        item, bad_selector=failure == 'selector', wrong_owner=failure == 'source_owner',
        role_override='system' if failure == 'invalid_role' else None))
    manager = Responses([corrupted(item), second])
    with pytest.raises(CodexError):
        knowledge.extract_batch(engine, 1, manager, window, knowledge.extraction_configuration(manager))
    assert len(manager.calls) == 2 and rows(engine) == []


def test_cancellation_before_fallback_prevents_second_request(quote_batch, monkeypatch):
    engine, window, item = quote_batch
    cancelled = False

    def first_response(*_):
        nonlocal cancelled
        cancelled = True
        return corrupted(item)

    manager = Responses([first_response, selected(item)])

    def cancel():
        if cancelled:
            raise progress.GenerationCancelled()

    monkeypatch.setattr(progress, 'check_cancelled', cancel)
    with pytest.raises(progress.GenerationCancelled):
        knowledge.extract_batch(engine, 1, manager, window, knowledge.extraction_configuration(manager))
    assert len(manager.calls) == 1 and rows(engine) == []


@pytest.mark.parametrize('failure', ['invalid_role', 'foreign', 'invalid_json', 'transport'])
def test_non_quote_failures_do_not_retry(quote_batch, failure):
    engine, window, item = quote_batch
    output = deepcopy(item)
    if failure == 'invalid_role':
        output['evidence'][0]['role'] = 'system'
    elif failure == 'foreign':
        output['evidence'][0]['source_id'] = 'foreign'
    first = ('not JSON' if failure == 'invalid_json' else
             CodexError('Transport or authentication failed.') if failure == 'transport' else response(output))
    manager = Responses([first, selected(item)])
    with pytest.raises(CodexError):
        knowledge.extract_batch(engine, 1, manager, window, knowledge.extraction_configuration(manager))
    assert len(manager.calls) == 1 and rows(engine) == []


@pytest.mark.parametrize('mismatch', ['excerpt', 'speaker'])
def test_invalid_cached_citation_does_not_trigger_fresh_generation(quote_batch, mismatch):
    engine, window, item = quote_batch
    manager = Responses([response(item)])
    configuration = knowledge.extraction_configuration(manager)
    knowledge.extract_batch(engine, 1, manager, window, configuration)
    with Session(engine) as db:
        row = db.scalar(select(ProjectHistoryBatch))
        envelope = deepcopy(row.evidence)
        invalid = corrupted(item) if mismatch == 'excerpt' else response(item)
        if mismatch == 'speaker':
            invalid['items'][0]['evidence'][0]['role'] = 'user'
        envelope[0]['raw'] = json.dumps(invalid)
        row.evidence = envelope
        db.commit()
    replay_manager = Responses([])
    with pytest.raises(CodexError):
        knowledge.extract_batch(engine, 1, replay_manager, window, configuration)
    assert replay_manager.calls == [] and len(rows(engine)) == 1


def test_oversized_fresh_quote_uses_one_bounded_selector_retry(quote_batch):
    engine, window, item = quote_batch
    original = 'x' * 1601
    window['sources'][0].update(text=original, revision=hashlib.sha256(original.encode()).hexdigest())
    item['evidence'][0]['excerpt'] = original

    def choose_bounded_quote(prompt, schema):
        payload = json.loads(prompt.rsplit('\n', 1)[1])
        quotes = payload['sources'][0]['literal_quotes']
        assert ''.join(quote['text'] for quote in quotes) == original
        assert all(len(quote['text']) <= 1600 for quote in quotes)
        value = deepcopy(item)
        value['evidence'][0].pop('source_id')
        value['evidence'][0]['excerpt'] = quotes[0]['id']
        return response(value)

    manager = Responses([response(item), choose_bounded_quote])
    items, hit = knowledge.extract_batch(engine, 1, manager, window, knowledge.extraction_configuration(manager))
    assert not hit and len(manager.calls) == 2
    assert items[0]['evidence'][0]['excerpt'] == 'x' * 1600
    assert len(rows(engine)) == 1


def candidate_bundle(sources):
    return {'project': {'id': 1}, 'sources': sources,
            'manifest': {'fingerprint': 'quote-retry-fixture', 'entries': [
                {'source_key': key, 'availability': 'available', 'revision': key}
                for key in dict.fromkeys(source['source_key'] for source in sources)]},
            'coverage': {'processed': ['conversation'], 'unprocessed': [], 'unavailable': [],
                         'excluded': [], 'limitations': []}}


def select_test_windows(monkeypatch, windows):
    from backend.app.integrations.codex import knowledge_sources, knowledge_profiles
    monkeypatch.setattr(knowledge_sources, 'build_windows', lambda *args, **kwargs: deepcopy(windows))
    monkeypatch.setattr(knowledge_profiles, 'select_profile_windows',
                        lambda bundle, selected, profile: (selected, bundle['coverage'], {'fixture': 'all'}))


def test_candidate_counts_retry_request_and_resets_metrics_on_cached_replay(quote_batch, monkeypatch):
    engine, window, item = quote_batch
    select_test_windows(monkeypatch, [window])
    manager = Responses([corrupted(item), selected(item)])
    bundle = candidate_bundle(window['sources'])
    candidate = knowledge.build_candidate(engine, 1, manager, bundle)
    assert candidate['diagnostics']['ai_requests'] == 2
    assert candidate['diagnostics']['quote_selector_retries'] == 1
    assert candidate['diagnostics']['cache_hits'] == 0
    assert len(manager.calls) == 2

    replay = knowledge.build_candidate(engine, 1, manager, bundle, previous=candidate)
    assert replay['diagnostics']['ai_requests'] == 0
    assert replay['diagnostics']['cache_hits'] == 1
    assert 'quote_selector_retries' not in replay['diagnostics']
    assert len(manager.calls) == 2
    assert len(replay['items']) == len(candidate['items'])
    assert [{key: actual[key] for key in original}
            for actual, original in zip(replay['items'], candidate['items'])] == candidate['items']


def test_candidate_counts_formatting_retry_and_reuses_validated_result(quote_batch, monkeypatch):
    engine, window, item = quote_batch
    select_test_windows(monkeypatch, [window])
    manager = Responses([JSONFormattingError('Formatting stalled'), response(item)])
    bundle = candidate_bundle(window['sources'])

    candidate = knowledge.build_candidate(engine, 1, manager, bundle)
    assert candidate['diagnostics']['ai_requests'] == 2
    assert candidate['diagnostics']['formatting_retries'] == 1
    assert len(candidate['items']) == 1
    assert 'Return compact JSON with no indentation' in manager.calls[1][0]

    replay = knowledge.build_candidate(engine, 1, manager, bundle, previous=candidate)
    assert replay['diagnostics']['cache_hits'] == 1
    assert replay['diagnostics']['ai_requests'] == 0
    assert 'formatting_retries' not in replay['diagnostics']
    assert len(manager.calls) == 2


def test_parallel_candidate_counts_each_worker_retry_without_cross_run_leak(quote_batch, monkeypatch):
    from collections import Counter
    from contextlib import contextmanager
    from threading import Barrier, Lock

    engine, window, item = quote_batch
    windows = [{'sources': [deepcopy(source)], 'primary_ids': [source['id']], 'context_ids': []}
               for source in window['sources']]
    select_test_windows(monkeypatch, windows)
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '2')

    class ParallelResponses:
        def __init__(self):
            self.calls = Counter()
            self.lock = Lock()
            self.barrier = Barrier(2)

        @contextmanager
        def independent_generation(self):
            yield self

        def generate_extraction(self, prompt, schema):
            payload = json.loads(prompt.rsplit('\n', 1)[1])
            source = payload['sources'][0]
            identifier = source['id']
            with self.lock:
                self.calls[identifier] += 1
            value = deepcopy(item)
            value['scope'] = identifier
            value['evidence'][0]['source_id'] = identifier
            if 'text' in source:
                self.barrier.wait(timeout=5)
                value['evidence'][0]['excerpt'] = 'A model-modified quotation absent from either source.'
            else:
                value['evidence'][0].pop('source_id')
                value['evidence'][0]['excerpt'] = source['literal_quotes'][0]['id']
            return json.dumps(response(value))

    manager = ParallelResponses()
    bundle = candidate_bundle(window['sources'])
    candidate = knowledge.build_candidate(engine, 1, manager, bundle)
    assert candidate['diagnostics']['extraction_workers'] == 2
    assert candidate['diagnostics']['ai_requests'] == 4
    assert candidate['diagnostics']['quote_selector_retries'] == 2
    assert candidate['diagnostics']['cache_hits'] == 0
    assert manager.calls == {'source:1': 2, 'source:2': 2}
    assert len(candidate['items']) == 2

    replay = knowledge.build_candidate(engine, 1, manager, bundle, previous=candidate)
    assert replay['diagnostics']['ai_requests'] == 0
    assert replay['diagnostics']['cache_hits'] == 2
    assert 'quote_selector_retries' not in replay['diagnostics']
    assert manager.calls == {'source:1': 2, 'source:2': 2}
    assert len(replay['items']) == len(candidate['items'])
    assert [{key: actual[key] for key in original}
            for actual, original in zip(replay['items'], candidate['items'])] == candidate['items']


@pytest.mark.parametrize('violation', ['foreign', 'invalid_role', 'classification'])
def test_later_non_quote_errors_are_not_masked_by_earlier_quote_error(quote_batch, violation):
    engine, window, item = quote_batch
    invalid = corrupted(item)
    if violation in ('foreign', 'invalid_role'):
        ref = {'source_id': 'source:2', 'role': 'assistant',
               'excerpt': window['sources'][1]['text']}
        if violation == 'foreign':
            ref['source_id'] = 'foreign-source'
        else:
            ref['role'] = 'system'
        invalid['items'][0]['evidence'].append(ref)
    else:
        later = deepcopy(item)
        later['category'] = 'INVALID'
        invalid['items'].append(later)
    manager = Responses([invalid])
    with pytest.raises(CodexError) as error:
        knowledge.extract_batch(engine, 1, manager, window, knowledge.extraction_configuration(manager))
    assert not isinstance(error.value, knowledge.EvidenceExcerptError)
    assert len(manager.calls) == 1
    with Session(engine) as db:
        assert db.scalars(select(ProjectHistoryBatch)).all() == []


def test_unsupported_agreement_gets_one_shared_repair_and_canonical_cache(quote_batch):
    engine, window, item = quote_batch
    unsupported = deepcopy(item)
    unsupported.update(design_status='AGREED', agreement_basis='direct_user')
    manager = Responses([response(unsupported), selected(item)])
    config = knowledge.extraction_configuration(manager)
    items, hit = knowledge.extract_batch(engine, 1, manager, window, config)
    assert not hit and len(manager.calls) == 2
    assert 'previous extraction failed agreement validation' in manager.calls[1][0]
    assert items[0]['design_status'] == 'PROPOSED'
    assert items[0]['agreement_basis'] == 'none'
    replay, hit = knowledge.extract_batch(engine, 1, manager, window, config)
    assert hit and replay == items and len(manager.calls) == 2


@pytest.mark.parametrize('first_error', ['agreement', 'quote'])
@pytest.mark.parametrize('status', ['AGREED', 'REJECTED'])
def test_second_unsupported_agreement_is_omitted_without_more_retries_or_cache(quote_batch, first_error, status):
    engine, window, item = quote_batch
    unsupported = deepcopy(item)
    unsupported.update(design_status=status, agreement_basis='direct_user')
    first = response(unsupported) if first_error == 'agreement' else corrupted(item)
    manager = Responses([first, selected(unsupported)])
    items, hit = knowledge.extract_batch(engine, 1, manager, window, knowledge.extraction_configuration(manager))
    assert items == [] and not hit
    assert len(manager.calls) == 2 and rows(engine) == []


def test_missing_same_conversation_proposal_preserves_valid_items_and_clean_window_cache(quote_batch, monkeypatch):
    engine, window, item = quote_batch
    proposal, observation = window['sources']
    acceptance = {**proposal, 'id': 'source:acceptance', 'role': 'user', 'text': 'Yes.',
                  'conversation_id': 'other-conversation', 'source_key': 'other-conversation', 'order': 3}
    acceptance['revision'] = hashlib.sha256(acceptance['text'].encode()).hexdigest()
    window = {'sources': [proposal, acceptance], 'primary_ids': [proposal['id'], acceptance['id']],
              'context_ids': []}
    clean_window = {'sources': [observation], 'primary_ids': [observation['id']], 'context_ids': []}
    select_test_windows(monkeypatch, [window, clean_window])
    unsupported = {**deepcopy(item), 'statement': 'The user accepted the expression.',
                   'design_status': 'AGREED', 'agreement_basis': 'proposal_acceptance'}
    unsupported['evidence'].append({'source_id': acceptance['id'], 'role': 'user', 'excerpt': acceptance['text']})
    clean_item = {**deepcopy(item), 'statement': observation['text'], 'evidence': [
        {'source_id': observation['id'], 'role': 'assistant', 'excerpt': observation['text']}]}

    def repair(prompt, schema):
        payload = json.loads(prompt.rsplit('\n', 1)[1])
        selectors = {source['id']: source['literal_quotes'][0]['id'] for source in payload['sources']}
        repaired = deepcopy([unsupported, item])
        for candidate in repaired:
            for ref in candidate['evidence']:
                ref['excerpt'] = selectors[ref.pop('source_id')]
        return {'items': repaired}

    manager = Responses([{'items': [unsupported, item]}, repair, response(clean_item), response(item)])
    bundle = candidate_bundle([proposal, acceptance, observation])
    candidate = knowledge.build_candidate(engine, 1, manager, bundle)

    assert {value['statement'] for value in candidate['items']} == {item['statement'], clean_item['statement']}
    assert all(value['design_status'] == 'PROPOSED' for value in candidate['items'])
    assert any('1' in note and 'agreement' in note.lower() for note in candidate['coverage']['limitations'])
    assert candidate['diagnostics']['unsupported_agreement_items'] == 1
    assert candidate['diagnostics']['ai_requests'] == 3
    assert candidate['diagnostics']['cache_hits'] == 0
    assert len(rows(engine)) == 1

    replay = knowledge.build_candidate(engine, 1, manager, bundle, previous=candidate)
    assert replay['diagnostics']['ai_requests'] == 1
    assert replay['diagnostics']['cache_hits'] == 1
    assert replay['coverage']['limitations'] == []
    assert not replay['diagnostics'].get('unsupported_agreement_items')
    assert len(rows(engine)) == 2
    assert len(manager.calls) == 4


def test_repeated_speaker_mismatch_omits_the_whole_item_and_keeps_clean_cache(quote_batch, monkeypatch):
    engine, window, item = quote_batch
    proposal, observation = window['sources']
    clean_window = {'sources': [observation], 'primary_ids': [observation['id']], 'context_ids': []}
    select_test_windows(monkeypatch, [window, clean_window])

    invalid_item = deepcopy(item)
    invalid_item['statement'] = 'The user stated the expression.'
    invalid_item['evidence'].append({**invalid_item['evidence'][0], 'role': 'user'})
    clean_item = {**deepcopy(item), 'statement': observation['text'], 'evidence': [
        {'source_id': observation['id'], 'role': observation['role'], 'excerpt': observation['text']},
    ]}

    def retry_with_mismatch(prompt, schema):
        repaired = selected(invalid_item)(prompt, schema)
        payload = json.loads(prompt.rsplit('\n', 1)[1])
        repaired['items'][0]['evidence'][1] = {
            'role': 'user', 'excerpt': payload['sources'][0]['literal_quotes'][0]['id'],
        }
        repaired['items'].extend(selected(item)(prompt, schema)['items'])
        return repaired

    manager = Responses([{'items': [invalid_item, item]}, retry_with_mismatch,
                         response(clean_item), response(item)])
    bundle = candidate_bundle([proposal, observation])

    candidate = knowledge.build_candidate(engine, 1, manager, bundle)

    assert {value['statement'] for value in candidate['items']} == {item['statement'], clean_item['statement']}
    assert all(ref['role'] == 'assistant' for value in candidate['items'] for ref in value['evidence'])
    assert candidate['diagnostics']['invalid_evidence_items'] == 1
    assert candidate['diagnostics']['quote_selector_retries'] == 1
    assert candidate['diagnostics']['ai_requests'] == 3
    assert candidate['coverage']['limitations']
    assert len(rows(engine)) == 1

    replay = knowledge.build_candidate(engine, 1, manager, bundle, previous=candidate)
    assert replay['diagnostics']['cache_hits'] == 1
    assert replay['diagnostics']['ai_requests'] == 1
    assert not replay['diagnostics'].get('invalid_evidence_items')
    assert replay['coverage']['limitations'] == []
    assert len(rows(engine)) == 2 and len(manager.calls) == 4


@pytest.mark.parametrize('phase', ['fresh', 'repair'])
def test_speaker_mismatch_cannot_mask_a_later_foreign_citation(quote_batch, phase):
    engine, window, item = quote_batch
    mismatched = response(item)
    mismatched['items'][0]['evidence'][0]['role'] = 'user'
    if phase == 'fresh':
        foreign = response(item)['items'][0]
        foreign['evidence'][0]['source_id'] = 'foreign'
        mismatched['items'].append(foreign)
        manager = Responses([mismatched])
    else:
        def repair(prompt, schema):
            result = selected(item, role_override='user')(prompt, schema)
            result['items'].extend(selected(item, bad_selector=True)(prompt, schema)['items'])
            return result
        manager = Responses([mismatched, repair])

    with pytest.raises(CodexError, match='foreign evidence|unknown quote selector'):
        knowledge.extract_batch(engine, 1, manager, window, knowledge.extraction_configuration(manager))
    assert len(manager.calls) == (1 if phase == 'fresh' else 2)
    assert rows(engine) == []


@pytest.mark.parametrize('hard_error', ['selector', 'invalid_role', 'shape'])
def test_unsupported_agreement_cannot_mask_later_hard_error_in_repair(quote_batch, hard_error):
    engine, window, item = quote_batch
    unsupported = {**deepcopy(item), 'design_status': 'AGREED', 'agreement_basis': 'direct_user'}

    def repair(prompt, schema):
        repaired = selected(unsupported)(prompt, schema)
        invalid = selected(item, bad_selector=hard_error == 'selector',
                           role_override='system' if hard_error == 'invalid_role' else None)(prompt, schema)
        if hard_error == 'shape':
            invalid['items'][0].pop('scope')
        repaired['items'].extend(invalid['items'])
        return repaired

    manager = Responses([response(unsupported), repair])
    with pytest.raises(CodexError) as error:
        knowledge.extract_batch(engine, 1, manager, window, knowledge.extraction_configuration(manager))
    assert not isinstance(error.value, knowledge.EvidenceAgreementError)
    assert len(manager.calls) == 2 and rows(engine) == []


@pytest.mark.parametrize('hard_error', ['foreign', 'invalid_role', 'shape'])
def test_agreement_error_cannot_mask_later_hard_error(quote_batch, hard_error):
    engine, window, item = quote_batch
    unsupported = deepcopy(item)
    unsupported.update(design_status='AGREED', agreement_basis='direct_user')
    later = deepcopy(item)
    if hard_error == 'foreign':
        later['evidence'][0]['source_id'] = 'foreign'
    elif hard_error == 'invalid_role':
        later['evidence'][0]['role'] = 'system'
    else:
        later.pop('scope')
    manager = Responses([{'items': [unsupported, later]}])
    with pytest.raises(CodexError) as error:
        knowledge.extract_batch(engine, 1, manager, window, knowledge.extraction_configuration(manager))
    assert not isinstance(error.value, (knowledge.EvidenceAgreementError, knowledge.EvidenceExcerptError))
    assert len(manager.calls) == 1 and rows(engine) == []


def test_repair_can_retain_agreement_when_actual_user_evidence_is_cited(quote_batch):
    engine, window, item = quote_batch
    user = {**window['sources'][0], 'id': 'user:decision', 'role': 'user',
            'text': 'Use the stated expression for the change calculation.', 'order': 3}
    window['sources'].append(user)
    window['primary_ids'].append(user['id'])
    unsupported = deepcopy(item)
    unsupported.update(design_status='AGREED', agreement_basis='direct_user')
    def repair(prompt, schema):
        payload = json.loads(prompt.rsplit('\n', 1)[1])
        quote = payload['sources'][-1]['literal_quotes'][0]
        result = deepcopy(unsupported)
        result['evidence'] = [{'role': 'user', 'excerpt': quote['id']}]
        return response(result)
    manager = Responses([response(unsupported), repair])
    items, _ = knowledge.extract_batch(engine, 1, manager, window, knowledge.extraction_configuration(manager))
    assert items[0]['design_status'] == 'AGREED'
    assert items[0]['evidence'][0]['source_id'] == user['id']
    assert items[0]['evidence'][0]['excerpt'] == user['text']


def test_cached_unsupported_agreement_does_not_invoke_repair(quote_batch):
    engine, window, item = quote_batch
    manager = Responses([response(item)])
    config = knowledge.extraction_configuration(manager)
    knowledge.extract_batch(engine, 1, manager, window, config)
    unsupported = deepcopy(item)
    unsupported.update(design_status='AGREED', agreement_basis='direct_user')
    with Session(engine) as db:
        cached = db.scalars(select(ProjectHistoryBatch)).one()
        cached.evidence = [{'raw': json.dumps(response(unsupported))}]
        db.commit()
    with pytest.raises(knowledge.EvidenceAgreementError):
        knowledge.extract_batch(engine, 1, manager, window, config)
    assert len(manager.calls) == 1
