"""Topic navigation tests with mocked semantic responses, no real protocol calls."""
from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
import json
import threading

import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project
from backend.app.integrations.codex import topic_groups as topics
from backend.app.integrations.codex.protocol import CodexError


@pytest.fixture
def engine(tmp_path):
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add_all([Project(name='One'), Project(name='Two')])
        db.commit()
    return engine


def record(identifier, statement='Use SQLite.'):
    return {'id': identifier, 'statement': statement, 'category': 'DECISION',
            'design_status': 'AGREED', 'scope': 'storage'}


class Manager:
    def __init__(self):
        self.calls = []

    def generate_brief(self, prompt, schema):
        payload = json.loads(prompt.rsplit('\n', 1)[1])
        self.calls.append(payload)
        existing = payload['topics']
        return json.dumps({
            'groups': [] if existing else [{'id': 'storage', 'label': 'Storage',
                                           'description': 'Project storage choices.'}],
            'assignments': [{'item_id': value['id'], 'group_ids': [existing[0]['id'] if existing else 'storage']}
                            for value in payload['records']],
        })


def test_exact_reuse_delta_only_and_immutable_records(engine):
    manager = Manager()
    records = [record('a'), record('b')]
    original = deepcopy(records)
    first = topics.group_records(engine, 1, manager, records)
    assert records == original
    assert first['topic_groups'][0]['item_ids'] == ['a', 'b']
    warm = topics.group_records(engine, 1, manager, records)
    assert len(manager.calls) == 1
    assert warm['topic_grouping']['cache_hits'] == 1
    assert warm['topic_groups'] == first['topic_groups']
    reused = topics.group_records(engine, 1, manager, records, previous=first)
    assert len(manager.calls) == 1
    assert reused['topic_grouping']['reused_items'] == 2
    delta = topics.group_records(engine, 1, manager, [record('a'), record('b', 'Use WAL.'), record('c')], previous=first)
    assert [value['id'] for value in manager.calls[-1]['records']] == ['b', 'c']
    assert delta['topic_groups'][0]['id'] == first['topic_groups'][0]['id']
    assert delta['topic_groups'][0]['item_ids'] == ['a', 'b', 'c']


def test_bounded_batches_have_independent_warm_cache(engine, monkeypatch):
    monkeypatch.setattr(topics, 'MAX_BATCH_ITEMS', 2)
    manager = Manager()
    records = [record(str(index)) for index in range(5)]
    first = topics.group_records(engine, 1, manager, records)
    assert [len(call['records']) for call in manager.calls] == [2, 2, 1]
    assert all(call['topics'] == [] for call in manager.calls)
    assert len(first['topic_groups']) == 1
    warm = topics.group_records(engine, 1, manager, records)
    assert len(manager.calls) == 3
    assert warm['topic_grouping']['cache_hits'] == 3
    assert warm['topic_groups'] == first['topic_groups']


def test_configuration_and_project_scope_separate_cache(engine):
    manager = Manager()
    first = topics.group_records(engine, 1, manager, [record('a')])
    manager.generation_effort = 'high'
    topics.group_records(engine, 1, manager, [record('a')], previous=first)
    topics.group_records(engine, 2, manager, [record('a')])
    assert len(manager.calls) == 3
    with pytest.raises(CodexError, match='selected project'):
        topics.group_records(engine, 2, manager, [record('a')], previous=first)
    with pytest.raises(CodexError, match='foreign'):
        topics.group_records(engine, 1, manager, [{**record('a'), 'project_id': 2}])


def test_empty_records_make_no_model_call(engine):
    manager = Manager()
    result = topics.group_records(engine, 1, manager, [])
    assert result['topic_groups'] == []
    assert manager.calls == []


def test_multimembership_and_unassigned_are_valid():
    raw = json.dumps({'groups': [
        {'id': 'a', 'label': 'Storage', 'description': 'Storage choices.'},
        {'id': 'b', 'label': 'Operations', 'description': 'Operational choices.'}],
        'assignments': [{'item_id': 'one', 'group_ids': ['a', 'b']},
                        {'item_id': 'two', 'group_ids': []}]})
    taxonomy, memberships = topics._validate_reply(raw, [record('one'), record('two')], [])
    assert len(taxonomy) == 2
    assert len(memberships['one']) == 2
    assert memberships['two'] == []


@pytest.mark.parametrize('assignments', [
    [], [{'item_id': 'foreign', 'group_ids': []}],
    [{'item_id': 'one', 'group_ids': ['foreign']}],
    [{'item_id': 'one', 'group_ids': ['a', 'a']}],
])
def test_invalid_model_membership_fails_closed(assignments):
    raw = json.dumps({'groups': [{'id': 'a', 'label': 'Storage', 'description': 'Storage choices.'}],
                      'assignments': assignments})
    with pytest.raises(CodexError):
        topics._validate_reply(raw, [record('one')], [])


def test_saved_membership_rejects_unknown_ids():
    with pytest.raises(CodexError, match='foreign'):
        topics.validate_topic_groups([{'id': 'a', 'label': 'Storage', 'description': 'Storage choices.',
                                       'item_ids': ['foreign']}], ['one'])


def test_parallel_batches_use_independent_transports_and_context(engine, monkeypatch):
    from backend.app.integrations.codex import knowledge
    marker = ContextVar('topic-test-marker', default=None)
    monkeypatch.setattr(topics, 'MAX_BATCH_ITEMS', 1)
    monkeypatch.setattr(knowledge, '_extraction_workers', lambda manager: 2)
    barrier = threading.Barrier(2)

    class ParallelManager(Manager):
        def __init__(self):
            super().__init__()
            self.transports = 0

        @contextmanager
        def independent_generation(self):
            self.transports += 1
            yield self

        def generate_brief(self, prompt, schema):
            assert marker.get() == 'copied'
            barrier.wait(timeout=3)
            return super().generate_brief(prompt, schema)

    manager = ParallelManager()
    token = marker.set('copied')
    try:
        result = topics.group_records(engine, 1, manager, [record('a'), record('b')])
    finally:
        marker.reset(token)
    assert manager.transports == 2
    assert result['topic_grouping']['workers'] == 2
    assert result['topic_groups'][0]['item_ids'] == ['a', 'b']


def test_invalid_reply_is_never_cached(engine):
    class InvalidManager(Manager):
        def generate_brief(self, prompt, schema):
            return '{}'

    with pytest.raises(CodexError):
        topics.group_records(engine, 1, InvalidManager(), [record('a')])
    manager = Manager()
    topics.group_records(engine, 1, manager, [record('a')])
    assert len(manager.calls) == 1


def test_single_oversized_record_fails_explicitly(engine):
    manager = Manager()
    with pytest.raises(CodexError, match='input limit'):
        topics.group_records(engine, 1, manager, [record('a', 'x' * topics.MAX_BATCH_CHARS)])
    assert manager.calls == []


@pytest.mark.parametrize('corrupt', [
    lambda previous: previous.update(topic_grouping=['invalid']),
    lambda previous: previous['topic_grouping'].update(project_id=2),
    lambda previous: previous['topic_grouping'].update(record_fingerprints=[]),
    lambda previous: previous['topic_grouping'].update(taxonomy=[]),
    lambda previous: previous['topic_groups'][0].update(description='Changed independently.'),
    lambda previous: previous['topic_groups'][0]['item_ids'].append('foreign'),
])
def test_corrupted_reusable_metadata_fails_closed(engine, corrupt):
    manager = Manager()
    previous = topics.group_records(engine, 1, manager, [record('a')])
    corrupt(previous)
    with pytest.raises(CodexError):
        topics.group_records(engine, 1, manager, [record('a')], previous=previous)
    assert len(manager.calls) == 1


def test_transport_failure_preserves_original_error_and_does_not_cache(engine):
    class FailedManager(Manager):
        def generate_brief(self, prompt, schema):
            raise CodexError('transport disconnected')

    with pytest.raises(CodexError, match='transport disconnected'):
        topics.group_records(engine, 1, FailedManager(), [record('a')])
    manager = Manager()
    topics.group_records(engine, 1, manager, [record('a')])
    assert len(manager.calls) == 1


def test_cancelled_generation_never_calls_model(engine, monkeypatch):
    def cancelled():
        raise CodexError('cancelled before grouping')

    monkeypatch.setattr(topics.progress, 'check_cancelled', cancelled)
    manager = Manager()
    with pytest.raises(CodexError, match='cancelled before grouping'):
        topics.group_records(engine, 1, manager, [record('a')])
    assert manager.calls == []

@pytest.mark.parametrize('metadata', [None, [], ''])
def test_falsey_malformed_metadata_is_explicit_error(engine, metadata):
    with pytest.raises(CodexError, match='metadata is invalid'):
        topics.group_records(engine, 1, Manager(), [record('a')],
                             previous={'topic_grouping': metadata})


def test_deep_json_is_explicit_error(monkeypatch):
    def reject(_):
        raise RecursionError('nested input')
    monkeypatch.setattr(topics.json, 'loads', reject)
    with pytest.raises(CodexError, match='invalid JSON'):
        topics._validate_reply('{}', [], [])
