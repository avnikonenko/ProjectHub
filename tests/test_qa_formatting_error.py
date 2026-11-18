"""Real generation lifecycle with a fake App Server client; no live model calls."""
from contextlib import nullcontext
from copy import deepcopy
import json

import pytest
from sqlalchemy import create_engine

from backend.app.models import Base
from backend.app.integrations.codex import manager as module
from backend.app.integrations.codex import progress, qa
from backend.app.integrations.codex.protocol import CodexError, JSONFormattingError


ERROR_TEXT = ('Codex emitted excessive whitespace while formatting a JSON response. '
              'Generation stopped.')


class GenerationClient:
    def __init__(self, *, on_event, on_disconnect, failure, late):
        self.on_event, self.on_disconnect = on_event, on_disconnect
        self.failure, self.late = failure, late
        self.closed = False
        self.calls = []

    def emit(self):
        scope = {'threadId': 'ephemeral', 'turnId': 'turn', 'itemId': 'answer'}
        if self.failure == 'formatting':
            # No item or turn completion: the partial JSON cannot become an answer.
            self.on_event('item/agentMessage/delta', {**scope, 'delta': '{'})
            self.on_event('item/agentMessage/delta', {**scope, 'delta': '\n' * 4096})
            self.on_event('item/agentMessage/delta', {**scope, 'delta': ' '})
        elif self.failure == 'disconnect':
            self.on_disconnect()
        elif self.failure == 'tool':
            self.on_event('projecthub/approvalDenied', scope)
        elif self.failure == 'failed-turn':
            # Matching text in a server error must never grant retry eligibility.
            self.on_event('turn/completed', {'threadId': 'ephemeral', 'turn': {
                'id': 'turn', 'status': 'failed', 'error': {'message': ERROR_TEXT},
            }})
        elif isinstance(self.failure, dict):
            for notification in self.failure.get('notifications', []):
                self.on_event('error', {'threadId': 'ephemeral', 'turnId': 'turn', 'willRetry': False,
                                        **notification})
            self.on_event('turn/completed', {'threadId': 'ephemeral', 'turn': {
                'id': 'turn', 'status': 'failed', 'error': self.failure.get('error'),
            }})
        elif self.failure == 'success':
            self.on_event('item/completed', {**scope, 'item': {
                'type': 'agentMessage', 'phase': 'final_answer', 'text': '{"items":[]}',
            }})
            self.on_event('turn/completed', {'threadId': 'ephemeral', 'turn': {
                'id': 'turn', 'status': 'completed',
            }})

    def request(self, method, params):
        self.calls.append((method, params))
        if method == 'account/read':
            return {'account': {'type': 'chatgpt'}}
        if method == 'config/read':
            return {'config': {'mcp_servers': {}}}
        if method == 'mcpServerStatus/list':
            return {'data': [], 'nextCursor': None}
        if method == 'thread/start':
            return {'thread': {'id': 'ephemeral', 'ephemeral': True}}
        if method == 'turn/start':
            if not self.late:
                self.emit()
            return {'turn': {'id': 'turn'}}
        if method == 'thread/unsubscribe':
            return {'status': 'unsubscribed'}
        raise AssertionError(method)

    def close(self):
        self.closed = True


@pytest.fixture
def generation(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'formatting.sqlite3'))
    Base.metadata.create_all(engine)
    monkeypatch.setattr(module, 'availability', lambda *args: {
        'installed': True, 'app_server': True, 'authenticated': True, 'error': '',
    })
    manager = module.CodexManager(engine, tmp_path)
    clients = []

    def configure(failure='formatting', late=False):
        def factory(**callbacks):
            outcome = failure() if callable(failure) else failure
            client = GenerationClient(**callbacks, failure=outcome, late=late)
            clients.append(client)
            return client
        manager.client_factory = factory
        if late:
            def wait(done, timeout):
                assert not done.is_set()
                clients[-1].emit()
                assert done.is_set()
            monkeypatch.setattr(module, 'wait_for_generation', wait)
        return manager, clients

    yield configure
    manager.close()
    engine.dispose()


@pytest.mark.parametrize('late', [False, True])
@pytest.mark.parametrize('pooled', [False, True])
def test_guard_failure_has_distinct_type_and_closes_failed_transport(generation, late, pooled):
    manager, clients = generation(late=late)
    schema = {'type': 'object', 'properties': {'answer': {'type': 'string', 'maxLength': 400}}}
    with manager.generation_session() if pooled else nullcontext():
        with pytest.raises(JSONFormattingError) as caught:
            manager.generate_brief('untrusted reference', schema)
        assert isinstance(caught.value, CodexError)
        assert str(caught.value) == ERROR_TEXT
        assert all(client.closed for client in clients)
        turn = next(params for client in clients for method, params in client.calls if method == 'turn/start')
        assert turn['outputSchema'] == schema
        assert turn['approvalPolicy'] == 'never'
        assert turn['sandboxPolicy'] == {'type': 'readOnly', 'networkAccess': False}
        if pooled:
            with pytest.raises(CodexError, match='transport failed') as subsequent:
                manager.generate_brief('no reuse of a failed scope', schema)
            assert type(subsequent.value) is CodexError
    assert sum(method == 'turn/start' for client in clients for method, _ in client.calls) == 1


def test_fresh_independent_generation_recovers_without_reusing_failed_parent_scope(generation):
    manager, clients = generation()
    with manager.generation_session():
        with pytest.raises(JSONFormattingError):
            manager.generate_brief('first attempt', {})
        failed_clients = list(clients)
        assert all(client.closed for client in failed_clients)
        generation(failure='success')
        with manager.independent_generation() as worker:
            assert worker.generate_brief('compact JSON retry', {}) == '{"items":[]}'
            assert clients[-1] not in failed_clients
            assert not clients[-1].closed
        assert clients[-1].closed
        with pytest.raises(CodexError, match='transport failed'):
            manager.generate_brief('parent remains unusable', {})
    assert all(client.closed for client in clients)
    assert sum(method == 'turn/start' for client in clients for method, _ in client.calls) == 2


@pytest.mark.parametrize('extraction', [False, True])
def test_knowledge_retries_formatting_once_and_keeps_pooled_generation_usable(
        generation, monkeypatch, extraction):
    outcomes = iter(('formatting', 'success'))
    manager, clients = generation(failure=lambda: next(outcomes, 'success'))
    monkeypatch.setattr(manager, 'validate_generation', lambda *_: None)
    manager.account_key = 'fixture-account'
    selected = manager.for_generation('fixture-model', 'medium')
    schema = {
        'type': 'object',
        'properties': {'items': {'type': 'array', 'maxItems': 2, 'items': {
            'type': 'string', 'enum': ['draft', 'published'],
        }}},
    }
    reference = 'Original references for extraction or synthesis.'
    retries = []

    with selected.generation_session():
        assert module.request_knowledge_json(
            selected, reference, schema, extraction=extraction,
            on_retry=lambda: retries.append('formatting'),
        ) == '{"items":[]}'
        assert retries == ['formatting']
        failed, recovered = clients
        assert failed.closed
        first_turn = next(params for method, params in failed.calls if method == 'turn/start')
        retry_turn = next(params for method, params in recovered.calls if method == 'turn/start')
        structural = {'type': 'object', 'properties': {'items': {
            'type': 'array', 'items': schema['properties']['items']['items'],
        }}}
        assert first_turn['outputSchema'] == (structural if extraction else schema)
        assert retry_turn['outputSchema'] == structural
        assert first_turn['input'][0]['text'] != retry_turn['input'][0]['text']
        assert reference in retry_turn['input'][0]['text']
        assert 'compact json' in retry_turn['input'][0]['text'].lower()
        assert json.loads(retry_turn['input'][0]['text'].split('\n', 2)[1]) == schema
        for client, turn in ((failed, first_turn), (recovered, retry_turn)):
            start = next(params for method, params in client.calls if method == 'thread/start')
            assert start['model'] == 'fixture-model'
            assert turn['effort'] == 'medium'
        assert selected.generation_metadata['account'] == 'fixture-account'
        assert selected.generate_brief('next operation in the same scope', {}) == '{"items":[]}'

    assert all(client.closed for client in clients)
    assert sum(method == 'turn/start' for client in clients for method, _ in client.calls) == 3


def test_knowledge_does_not_retry_a_second_formatting_failure(generation):
    manager, clients = generation()
    with manager.generation_session(), pytest.raises(JSONFormattingError, match='Generation stopped\\.'):
        module.request_knowledge_json(manager, 'reference', {'type': 'object'})

    assert len(clients) == 2
    assert all(client.closed for client in clients)
    assert sum(method == 'turn/start' for client in clients for method, _ in client.calls) == 2


def test_knowledge_checks_cancellation_before_retrying_formatting_failure(generation, monkeypatch):
    manager, clients = generation()
    project_id = 9721
    progress.begin(project_id, 'knowledge')
    def cancel_during_formatting(prompt, schema):
        progress.current_cancel_event().set()
        raise JSONFormattingError(ERROR_TEXT)
    monkeypatch.setattr(manager, 'generate_brief', cancel_during_formatting)
    try:
        with pytest.raises(progress.GenerationCancelled):
            module.request_knowledge_json(manager, 'reference', {'type': 'object'})
        assert not clients
    finally:
        progress.finish(project_id)


def provider_error(info):
    return {'codexErrorInfo': info, 'message': 'private provider error',
            'additionalDetails': 'private diagnostic details'}


@pytest.mark.parametrize('notification,expected', [
    ({}, 'rate limit'),
    ({'threadId': 'foreign-thread'}, 'no supported failure reason'),
    ({'turnId': 'foreign-turn'}, 'no supported failure reason'),
    ({'willRetry': True}, 'no supported failure reason'),
])
@pytest.mark.parametrize('late', [False, True])
def test_matching_terminal_error_notification_explains_failed_turn(generation, notification, expected, late):
    info = {'responseStreamConnectionFailed': {'httpStatusCode': 429}}
    manager, clients = generation(failure={'notifications': [{
        'error': provider_error(info), **notification,
    }]}, late=late)

    with pytest.raises(CodexError, match=expected) as caught:
        manager.generate_brief('reference', {'type': 'object'})

    assert type(caught.value) is CodexError
    assert 'private' not in str(caught.value)
    assert len(clients) == 1 and clients[0].closed


@pytest.mark.parametrize('status,expected', [(401, 'HTTP 401'), (403, 'HTTP 403'), (429, 'rate limit')])
def test_known_http_failures_are_sanitized_and_not_retried(generation, status, expected):
    manager, clients = generation(failure={
        'error': provider_error({'responseStreamConnectionFailed': {'httpStatusCode': status}}),
        'notifications': [{'error': provider_error('serverOverloaded')}],
    })

    with manager.generation_session(), pytest.raises(CodexError, match=expected) as caught:
        module.request_knowledge_json(manager, 'reference', {'type': 'object'})

    assert type(caught.value) is CodexError
    assert 'private' not in str(caught.value)
    assert len(clients) == 1 and clients[0].closed


@pytest.mark.parametrize('retry_succeeds', [False, True])
@pytest.mark.parametrize('status', [200, 503])
def test_knowledge_retries_a_transient_turn_once_using_the_same_request(generation, retry_succeeds, status):
    error = {'error': provider_error({'responseStreamDisconnected': {'httpStatusCode': status}})}
    outcomes = iter([error, 'success' if retry_succeeds else error])
    manager, clients = generation(failure=lambda: next(outcomes))
    prompt = 'Original reference.'
    schema = {'type': 'object', 'properties': {'items': {'type': 'array', 'maxItems': 2}}}

    with manager.generation_session():
        if retry_succeeds:
            assert module.request_knowledge_json(manager, prompt, schema) == '{"items":[]}'
            assert clients[0].closed and not clients[1].closed
            assert manager.generate_brief('another request', schema) == '{"items":[]}'
        else:
            with pytest.raises(module.TransientGenerationError) as caught:
                module.request_knowledge_json(manager, prompt, schema)
            assert 'private' not in str(caught.value)

    assert len(clients) == 2
    for client in clients:
        params = next(value for method, value in client.calls if method == 'turn/start')
        assert params['input'] == [{'type': 'text', 'text': prompt}]
        assert params['outputSchema'] == schema
        assert client.closed
    assert sum(method == 'turn/start' for client in clients for method, _ in client.calls) == (3 if retry_succeeds else 2)


@pytest.mark.parametrize('stop', ['cancellation', 'budget'])
def test_knowledge_rechecks_cancellation_and_budget_before_transient_retry(generation, monkeypatch, stop):
    manager, clients = generation()
    project_id = 9722
    progress.begin(project_id, 'knowledge')
    retries = []

    def fail_and_stop(_prompt, _schema):
        if stop == 'cancellation':
            progress.current_cancel_event().set()
        else:
            monkeypatch.setitem(progress._clocks, (project_id, 'knowledge'), (0, 0))
        raise module.TransientGenerationError('The response stream disconnected.')

    monkeypatch.setattr(manager, 'generate_brief', fail_and_stop)
    error = progress.GenerationCancelled if stop == 'cancellation' else CodexError
    try:
        with pytest.raises(error, match='cancelled' if stop == 'cancellation' else 'time budget'):
            module.request_knowledge_json(manager, 'reference', {'type': 'object'},
                                          on_retry=lambda: retries.append(True))
        assert not retries and not clients
    finally:
        progress.finish(project_id)


@pytest.mark.parametrize('consolidation', [False, True])
def test_qa_schema_adapter_preserves_full_contract_and_structural_citation_constraints(generation, consolidation):
    manager, clients = generation(failure='success')
    canonical = deepcopy(qa._SCHEMA)
    item = canonical['properties']['items']['items']
    refs = item['properties']['source_ids']
    refs['items']['enum'] = ['1', '2']
    if consolidation:
        refs['maxItems'] = 6
        item['required'].append('variants')
        item['properties']['variants'] = {
            'type': 'array', 'maxItems': 2, 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['answer', 'source_ids'],
                'properties': {key: item['properties'][key] for key in ('answer', 'source_ids')},
            },
        }
    before = deepcopy(canonical)
    assert manager.generate_extraction('compact JSON retry with untrusted reference', canonical) == '{"items":[]}'
    turn = next(params for client in clients for method, params in client.calls if method == 'turn/start')
    prompt = turn['input'][0]['text']
    assert json.loads(prompt.split('\n', 2)[1]) == canonical
    assert prompt.endswith('compact JSON retry with untrusted reference')

    def compare(full, structural):
        if isinstance(full, dict):
            assert set(structural) == set(full) - {'minLength', 'maxLength', 'minItems', 'maxItems'}
            for key in structural:
                compare(full[key], structural[key])
        elif isinstance(full, list):
            assert len(full) == len(structural)
            for a, b in zip(full, structural):
                compare(a, b)
        else:
            assert structural == full

    compare(canonical, turn['outputSchema'])
    assert canonical == before
    assert all(client.closed for client in clients)


@pytest.mark.parametrize('failure', ['disconnect', 'tool', 'failed-turn', 'timeout'])
def test_unrelated_failures_remain_plain_codex_errors_and_close_client(generation, monkeypatch, failure):
    manager, clients = generation(failure=failure)
    if failure == 'timeout':
        def timeout(done, seconds):
            assert not done.is_set()
            raise CodexError('Synthetic timeout')
        monkeypatch.setattr(module, 'wait_for_generation', timeout)
    with pytest.raises(CodexError) as caught:
        manager.generate_brief('reference', {'type': 'object'})
    assert type(caught.value) is CodexError
    assert all(client.closed for client in clients)

    before = len(clients)
    with pytest.raises(CodexError) as knowledge_error:
        module.request_knowledge_json(manager, 'reference', {'type': 'object'})
    assert type(knowledge_error.value) is CodexError
    assert len(clients) == before + 1
