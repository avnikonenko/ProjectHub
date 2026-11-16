"""Catalog invalidation with mocked generation and disposable SQLite, no protocol calls."""
from contextlib import nullcontext
from copy import deepcopy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, ProjectHistoryBatch
from backend.app.integrations.codex import progress, topic_groups as topics
from backend.app.integrations.codex.manager import tag_generation_context
from backend.app.integrations.codex.protocol import CodexError


@pytest.fixture
def engine(tmp_path):
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add(Project(name='Catalog invalidation'))
        db.commit()
    yield engine
    engine.dispose()


def record(identifier, statement):
    return {'id': identifier, 'statement': statement, 'category': 'DECISION',
            'design_status': 'AGREED', 'scope': 'Project'}


def definition(label):
    return {'label': label, 'description': label + ' policy.'}


class Manager:
    def __init__(self):
        self.calls = []
        self.fail_recheck = False
        self.cancel_recheck = False

    def generate_brief(self, prompt, schema):
        payload = json.loads(prompt.rsplit('\n', 1)[1])
        self.calls.append(payload)
        if 'sample' in payload:
            if 'topics' not in payload:
                label = 'Storage' if any(r['statement'] == 'Storage' for r in payload['sample']) else 'Security'
                return json.dumps({'topics': [definition(label)]})
            additions = [definition('Security')] if any(
                r['statement'] == 'Security new' for r in payload['sample']) else []
            return json.dumps({'topics': additions})
        if 'groups' in schema['properties']:
            labels = {topic['label']: topic['id'] for topic in payload['topics']}
            added = []
            for label, trigger in [('Storage', 'Storage'), ('Security', 'Security new')]:
                if label not in labels and any(r['statement'] == trigger for r in payload['records']):
                    labels[label] = label.lower()
                    added.append({'id': labels[label], **definition(label)})
            return json.dumps({'groups': added, 'assignments': [
                {'item_id': r['id'], 'group_ids': self.matches(r, labels)} for r in payload['records']]})
        labels = {topic['label']: topic['i'] for topic in payload['topics']}
        is_recheck = 'Security' in labels and any(r['statement'] == 'Security old' for r in payload['records'])
        if is_recheck and self.cancel_recheck:
            progress.current_cancel_event().set()
        if is_recheck and self.fail_recheck:
            return '{"assignments":"invalid-recheck"}'
        packed = schema['properties']['assignments']['items']['type'] == 'array'
        return json.dumps({'assignments': [
            [r['i'], *self.matches(r, labels)] if packed else {'i': r['i'], 't': self.matches(r, labels)}
            for r in payload['records']]})

    @staticmethod
    def matches(record, labels):
        label = 'Security' if record['statement'].startswith('Security') else record['statement']
        return [labels[label]] if label in labels else []


def members(result):
    return {group['label']: group['item_ids'] for group in result['topic_groups']}


def initial_records():
    return [record('storage', 'Storage'), record('old', 'Security old'), record('other', 'Unrelated')]


@pytest.mark.parametrize('packed', [False, True])
def test_review_expansion_reassigns_old_negatives_once_and_preserves_positives(engine, packed):
    manager = Manager()
    records = initial_records()
    with tag_generation_context() if packed else nullcontext():
        first = topics.group_records(engine, 1, manager, records, compact_assignment=True)
        assert members(first) == {'Storage': ['storage']}
        original = deepcopy(first)
        start = len(manager.calls)
        expanded = topics.group_records(engine, 1, manager, [*records, record('new', 'Security new')],
                                        previous=first, compact_assignment=True)
        assert members(expanded) == {'Storage': ['storage'], 'Security': ['old', 'new']}
        assert expanded['topic_groups'][0] == first['topic_groups'][0]
        assert first == original
        assert expanded['topic_grouping']['ai_requests'] == 3
        review = next(p for p in manager.calls[start:] if 'sample' in p)
        assert [r['statement'] for r in review['sample']] == ['Security new']
        reassigned = manager.calls[-1]['records']
        assert {r['statement'] for r in reassigned} == {'Security old', 'Security new', 'Unrelated'}
        before_warm = len(manager.calls)
        replay = topics.group_records(engine, 1, manager, [*records, record('new', 'Security new')],
                                      previous=expanded, compact_assignment=True)
        assert replay['topic_groups'] == expanded['topic_groups']
        assert replay['topic_grouping']['ai_requests'] == 0
        assert len(manager.calls) == before_warm


@pytest.mark.parametrize('packed', [False, True])
def test_replacement_bootstrap_reassigns_surviving_negatives(engine, packed):
    manager = Manager()
    with tag_generation_context() if packed else nullcontext():
        first = topics.group_records(engine, 1, manager, initial_records(), compact_assignment=True)
        surviving = [record('old', 'Security old'), record('other', 'Unrelated'), record('new', 'Security new')]
        start = len(manager.calls)
        result = topics.group_records(engine, 1, manager, surviving, previous=first, compact_assignment=True)
        assert members(result) == {'Security': ['old', 'new']}
        assert result['topic_grouping']['ai_requests'] == 2
        assert len(manager.calls[start:]) == 2
        assert {r['statement'] for r in manager.calls[-1]['records']} == {
            'Security old', 'Security new', 'Unrelated'}
        replay = topics.group_records(engine, 1, manager, surviving, previous=result, compact_assignment=True)
        assert replay['topic_grouping']['ai_requests'] == 0


def test_legacy_catalog_expansion_uses_bounded_existing_topic_reassignment(engine, monkeypatch):
    manager = Manager()
    records = initial_records()
    first = topics.group_records(engine, 1, manager, records)
    monkeypatch.setattr(topics, 'MAX_COMPACT_BATCH_ITEMS', 1)
    start = len(manager.calls)
    result = topics.group_records(engine, 1, manager, [*records, record('new', 'Security new')], previous=first)
    assert members(result) == {'Storage': ['storage'], 'Security': ['old', 'new']}
    assert result['topic_grouping']['ai_requests'] == 3  # One new-record pass, two negative batches.
    assert [len(p['records']) for p in manager.calls[start:]] == [1, 1, 1]
    assert all('i' in p['records'][0] for p in manager.calls[start + 1:])
    replay = topics.group_records(engine, 1, manager, [*records, record('new', 'Security new')], previous=result)
    assert replay['topic_grouping']['ai_requests'] == 0


def test_preexisting_snapshot_without_catalog_marker_repairs_stale_negative_once(engine):
    manager = Manager()
    records = [*initial_records(), record('new', 'Security new')]
    first = topics.group_records(engine, 1, manager, records, compact_assignment=True)
    # Simulate a snapshot written before negative assignment catalog identities existed.
    first['topic_grouping'].pop('assignment_catalog_fingerprint')
    security = next(g for g in first['topic_groups'] if g['label'] == 'Security')
    security['item_ids'].remove('old')
    first['topic_grouping']['reviewed_fingerprints']['old'] = first['topic_grouping']['record_fingerprints']['old']
    result = topics.group_records(engine, 1, manager, records, previous=first, compact_assignment=True)
    assert members(result)['Security'] == ['old', 'new']
    assert result['topic_grouping']['reviewed_items'] == 0
    replay = topics.group_records(engine, 1, manager, records, previous=result, compact_assignment=True)
    assert replay['topic_grouping']['ai_requests'] == 0


@pytest.mark.parametrize('cancel', [False, True])
def test_failed_or_cancelled_negative_reassignment_preserves_previous_and_does_not_cache(engine, cancel):
    manager = Manager()
    records = initial_records()
    first = topics.group_records(engine, 1, manager, records)
    original = deepcopy(first)
    manager.fail_recheck = not cancel
    manager.cancel_recheck = cancel
    if cancel:
        progress.begin(1, 'tags')
    try:
        with pytest.raises(CodexError, match='cancelled' if cancel else 'invalid JSON or structure'):
            topics.group_records(engine, 1, manager, [*records, record('new', 'Security new')], previous=first)
    finally:
        if cancel:
            progress.finish(1)
    assert first == original
    # Only initial and successful new-record legacy responses are cacheable.
    with Session(engine) as db:
        assert len(db.scalars(select(ProjectHistoryBatch)).all()) == 2
    manager.fail_recheck = manager.cancel_recheck = False
    result = topics.group_records(engine, 1, manager, [*records, record('new', 'Security new')], previous=first)
    assert members(result)['Security'] == ['old', 'new']
    assert result['topic_grouping']['cache_hits'] >= 1


@pytest.mark.parametrize('change', ['order', 'definition'])
def test_catalog_identity_ignores_order_and_includes_definitions(engine, change):
    manager = Manager()
    records = [*initial_records(), record('new', 'Security new')]
    first = topics.group_records(engine, 1, manager, records, compact_assignment=True)
    if change == 'order':
        first['topic_grouping']['taxonomy'].reverse()
        first['topic_groups'].reverse()
    else:
        first['topic_grouping']['taxonomy'][0]['description'] = 'A revised existing definition.'
        first['topic_groups'][0]['description'] = 'A revised existing definition.'
    start = len(manager.calls)
    result = topics.group_records(engine, 1, manager, records, previous=first, compact_assignment=True)
    assert result['topic_grouping']['reviewed_items'] == 0
    assert len(manager.calls) - start == (1 if change == 'definition' else 0)
    assert result['topic_groups'] == first['topic_groups']
    assert result['topic_grouping']['assignment_catalog_fingerprint'] == topics._catalog_fingerprint(
        result['topic_grouping']['taxonomy'])


def test_empty_catalog_never_requests_assignment_with_zero_topic_schema(engine):
    manager = Manager()
    records = [record('other', 'Unrelated')]
    first = topics.group_records(engine, 1, manager, records)
    assert first['topic_groups'] == []
    first['topic_grouping'].pop('assignment_catalog_fingerprint')
    before = len(manager.calls)
    result = topics.group_records(engine, 1, manager, records, previous=first)
    assert result['topic_groups'] == []
    assert result['topic_grouping']['ai_requests'] == 0
    assert len(manager.calls) == before
    assert result['topic_grouping']['assignment_catalog_fingerprint'] == topics._catalog_fingerprint([])


def test_empty_compact_catalog_review_migration_assigns_empty_without_model_call(engine):
    manager = Manager()
    records = [record('other', 'Unrelated')]
    first = topics.group_records(engine, 1, manager, records, compact_assignment=True)
    assert first['topic_groups'] == []
    first['topic_grouping'].pop('assignment_catalog_fingerprint')
    first['topic_grouping'].pop('reviewed_fingerprints')
    before = len(manager.calls)
    result = topics.group_records(engine, 1, manager, records, previous=first, compact_assignment=True)
    assert result['topic_groups'] == []
    assert result['topic_grouping']['reviewed_items'] == 1
    assert len(manager.calls) == before + 1
    assert manager.calls[-1] == {'topics': [], 'sample': [{'category': 'DECISION', 'statement': 'Unrelated'}]}
    replay = topics.group_records(engine, 1, manager, records, previous=result, compact_assignment=True)
    assert replay['topic_grouping']['ai_requests'] == 0
