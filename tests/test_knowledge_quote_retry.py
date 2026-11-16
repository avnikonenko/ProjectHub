"""Fresh quote-format fallback is bounded; canonical cache validation stays strict."""
from copy import deepcopy
import hashlib
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import knowledge, progress
from backend.app.integrations.codex.protocol import CodexError
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


def selected(item, *, bad_selector=False, wrong_owner=False, wrong_role=False):
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
        if wrong_role:
            ref['role'] = 'user'
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


@pytest.mark.parametrize('failure', ['selector', 'source_owner', 'role', 'invalid_json'])
def test_second_failure_is_bounded_and_never_cached(quote_batch, failure):
    engine, window, item = quote_batch
    second = ('not JSON' if failure == 'invalid_json' else selected(
        item, bad_selector=failure == 'selector', wrong_owner=failure == 'source_owner',
        wrong_role=failure == 'role'))
    manager = Responses([corrupted(item), second])
    with pytest.raises(CodexError):
        knowledge.extract_batch(engine, 1, manager, window, knowledge.extraction_configuration(manager))
    assert len(manager.calls) == 2 and rows(engine) == []


def test_cancellation_before_fallback_prevents_second_request(quote_batch, monkeypatch):
    engine, window, item = quote_batch
    manager = Responses([corrupted(item), selected(item)])

    def cancel():
        raise progress.GenerationCancelled()

    monkeypatch.setattr(progress, 'check_cancelled', cancel)
    with pytest.raises(progress.GenerationCancelled):
        knowledge.extract_batch(engine, 1, manager, window, knowledge.extraction_configuration(manager))
    assert len(manager.calls) == 1 and rows(engine) == []


@pytest.mark.parametrize('failure', ['role', 'foreign', 'agreement', 'invalid_json', 'transport'])
def test_non_quote_failures_do_not_retry(quote_batch, failure):
    engine, window, item = quote_batch
    output = deepcopy(item)
    if failure == 'role':
        output['evidence'][0]['role'] = 'user'
    elif failure == 'foreign':
        output['evidence'][0]['source_id'] = 'foreign'
    elif failure == 'agreement':
        output.update(design_status='AGREED', agreement_basis='direct_user')
    first = ('not JSON' if failure == 'invalid_json' else
             CodexError('Transport or authentication failed.') if failure == 'transport' else response(output))
    manager = Responses([first, selected(item)])
    with pytest.raises(CodexError):
        knowledge.extract_batch(engine, 1, manager, window, knowledge.extraction_configuration(manager))
    assert len(manager.calls) == 1 and rows(engine) == []


def test_invalid_cached_quote_does_not_trigger_fresh_generation(quote_batch):
    engine, window, item = quote_batch
    manager = Responses([response(item)])
    configuration = knowledge.extraction_configuration(manager)
    knowledge.extract_batch(engine, 1, manager, window, configuration)
    with Session(engine) as db:
        row = db.scalar(select(ProjectHistoryBatch))
        envelope = deepcopy(row.evidence)
        envelope[0]['raw'] = json.dumps(corrupted(item))
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


@pytest.mark.parametrize('violation', ['foreign', 'role', 'classification', 'agreement'])
def test_later_non_quote_errors_are_not_masked_by_earlier_quote_error(quote_batch, violation):
    engine, window, item = quote_batch
    invalid = corrupted(item)
    if violation in ('foreign', 'role'):
        ref = {'source_id': 'source:2', 'role': 'assistant',
               'excerpt': window['sources'][1]['text']}
        if violation == 'foreign':
            ref['source_id'] = 'foreign-source'
        else:
            ref['role'] = 'user'
        invalid['items'][0]['evidence'].append(ref)
    else:
        later = deepcopy(item)
        if violation == 'classification':
            later['category'] = 'INVALID'
        else:
            later['design_status'] = 'AGREED'
            later['agreement_basis'] = 'direct_user'
        invalid['items'].append(later)
    manager = Responses([invalid])
    with pytest.raises(CodexError) as error:
        knowledge.extract_batch(engine, 1, manager, window, knowledge.extraction_configuration(manager))
    assert not isinstance(error.value, knowledge.EvidenceExcerptError)
    assert len(manager.calls) == 1
    with Session(engine) as db:
        assert db.scalars(select(ProjectHistoryBatch)).all() == []
