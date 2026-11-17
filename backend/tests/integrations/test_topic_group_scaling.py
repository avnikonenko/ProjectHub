"""Bounded topic catalog regressions using mocked semantics and disposable SQLite."""
import json

import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project
from backend.app.integrations.codex import topic_groups as topics
from backend.app.integrations.codex.protocol import CodexError


def records(count, start=0):
    return [{'id': f'item-{index}', 'statement': f'Subject {index}',
             'category': 'DECISION', 'design_status': 'AGREED', 'scope': 'project'}
            for index in range(start, start + count)]


@pytest.fixture
def engine(tmp_path):
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add(Project(name='Topics'))
        db.commit()
    yield engine
    engine.dispose()


class SemanticManager:
    generation_metadata = {'account': 'mock', 'model': 'mock', 'effort': 'low'}

    def __init__(self, topic_count):
        self.topic_count = topic_count
        self.calls = 0
        self.payloads = []
        self.fail = False

    def generate_brief(self, prompt, schema):
        assert not self.fail, 'Warm-cache run must not request generation'
        self.calls += 1
        payload = json.loads(prompt.rsplit('\n', 1)[1])
        self.payloads.append(payload)
        count = self.topic_count(payload) if callable(self.topic_count) else self.topic_count
        prefix = payload['records'][0]['id']
        definitions = [{'id': f'{prefix}-{index}', 'label': f'{prefix} topic {index}',
                        'description': 'A project subject.'} for index in range(count)]
        return json.dumps({'groups': definitions, 'assignments': [
            {'item_id': record['id'], 'group_ids': [group['id'] for group in definitions]}
            for record in payload['records']]})


def test_independent_batches_merge_42_topics_and_reuse_existing_cache(engine):
    manager = SemanticManager(21)
    initial = topics.group_records(engine, 1, manager, records(121))
    assert manager.calls == 2
    assert len(initial['topic_groups']) == 42
    assert initial['topic_grouping']['ai_requests'] == 2
    manager.fail = True
    warm = topics.group_records(engine, 1, manager, records(121))
    assert warm['topic_groups'] == initial['topic_groups']
    assert warm['topic_grouping']['ai_requests'] == 0
    assert warm['topic_grouping']['cache_hits'] == 2
    incremental = topics.group_records(engine, 1, manager, records(121), previous=initial)
    assert incremental['topic_groups'] == initial['topic_groups']
    assert incremental['topic_grouping']['reused_items'] == 121
    assert incremental['topic_grouping']['ai_requests'] == 0


def test_removed_records_retire_taxonomy_before_delta_classification(engine):
    manager = SemanticManager(lambda payload: 40 if payload['records'][0]['id'] == 'item-0' else 1)
    initial = topics.group_records(engine, 1, manager, records(40))
    assert len(initial['topic_groups']) == 40
    delta = topics.group_records(engine, 1, manager, records(1, start=100), previous=initial)
    assert manager.payloads[-1]['topics'] == []
    assert len(delta['topic_groups']) == len(delta['topic_grouping']['taxonomy']) == 1
    empty = topics.group_records(engine, 1, manager, [], previous=initial)
    assert empty['topic_groups'] == empty['topic_grouping']['taxonomy'] == []


def test_unchanged_untagged_records_remain_reused(engine):
    manager = SemanticManager(0)
    initial = topics.group_records(engine, 1, manager, records(1))
    manager.fail = True
    warm = topics.group_records(engine, 1, manager, records(1), previous=initial)
    assert warm['topic_grouping']['reused_items'] == 1
    assert warm['topic_grouping']['ai_requests'] == 0


def test_401_genuine_topics_fail_instead_of_dropping_memberships(engine):
    manager = SemanticManager(lambda payload: 1 if payload['records'][0]['id'] == 'item-1200' else 40)
    with pytest.raises(CodexError, match='exceeded the topic limit'):
        topics.group_records(engine, 1, manager, records(1201))
    assert manager.calls == 11


def test_complete_prompt_limit_includes_instructions_and_taxonomy(engine, monkeypatch):
    manager = SemanticManager(1)
    initial = topics.group_records(engine, 1, manager, records(1))
    monkeypatch.setattr(topics, 'ai_instructions', lambda: 'x' * topics.MAX_PROMPT_CHARS)
    with pytest.raises(CodexError, match='prompt exceeds the input limit'):
        topics.group_records(engine, 1, manager, records(1, start=100), previous=initial)
    assert manager.calls == 1


def test_reply_proposals_remain_bounded_to_40(engine):
    with pytest.raises(CodexError, match='invalid structure'):
        topics.group_records(engine, 1, SemanticManager(41), records(1))


def test_prompt_limit_counts_existing_taxonomy(engine, monkeypatch):
    manager = SemanticManager(1)
    initial = topics.group_records(engine, 1, manager, records(1))
    pending = records(1, start=100)
    prompt_without_topics = topics.PROMPT + topics.ai_instructions() + '\n' + json.dumps(
        {'topics': [], 'records': pending}, ensure_ascii=False, separators=(',', ':'))
    monkeypatch.setattr(topics, 'MAX_PROMPT_CHARS', len(prompt_without_topics) + 1)
    with pytest.raises(CodexError, match='prompt exceeds the input limit'):
        topics.group_records(engine, 1, manager, records(1) + pending, previous=initial)
    assert manager.calls == 1


def test_saved_catalog_is_validated_before_orphans_are_pruned(engine):
    manager = SemanticManager(1)
    initial = topics.group_records(engine, 1, manager, records(1))
    initial['topic_groups'][0]['item_ids'] = ['foreign-record']
    with pytest.raises(CodexError, match='missing or foreign records'):
        topics.group_records(engine, 1, manager, [], previous=initial)
    assert manager.calls == 1
