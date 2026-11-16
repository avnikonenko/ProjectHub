"""Follow-up tag batches overlap safely; no real model requests are used."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
import threading
import time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, ProjectHistoryBatch
from backend.app.integrations.codex import knowledge, progress, topic_groups as topics
from backend.app.integrations.codex.protocol import CodexError


_marker = ContextVar('reassignment-marker', default=None)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add(Project(name='Topics'))
        db.commit()
    monkeypatch.setattr(topics, 'MAX_COMPACT_BATCH_ITEMS', 1)
    monkeypatch.setattr(knowledge, '_extraction_workers', lambda manager: 2)
    records = [{'id': str(i), 'statement': f'Record {i}', 'category': 'REQUIREMENT',
                'design_status': 'PROPOSED', 'scope': 'Project'} for i in range(4)]
    yield engine, records
    engine.dispose()


class Manager:
    def __init__(self, *, failure=None):
        self.failure = failure
        self.calls = []
        self.followup_calls = []
        self.barrier = threading.Barrier(2)
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.transports = set()

    @contextmanager
    def independent_generation(self):
        owner, token = self, object()
        class Selected:
            def generate_brief(self, prompt, schema):
                return owner.reply(prompt, schema, token)
        yield Selected()

    def reply(self, prompt, schema, transport):
        assert _marker.get() == 'copied'
        payload = json.loads(prompt.rsplit('\n', 1)[1])
        with self.lock:
            self.calls.append(payload)
        if 'sample' in payload:
            label = 'Security' if 'topics' in payload else 'Storage'
            return json.dumps({'topics': [{'label': label, 'description': label + ' policy.'}]})
        labels = {row['label']: row['i'] for row in payload['topics']}
        followup = 'Security' in labels
        if followup:
            index = int(payload['records'][0]['statement'].split()[-1])
            with self.lock:
                self.followup_calls.append(index)
                self.transports.add(transport)
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                if index < 2:
                    self.barrier.wait(timeout=3)
                if self.failure and index == 0:
                    self.release.set()
                    if self.failure == 'cancel':
                        snapshot = progress.read(1, 'tags')
                        progress.cancel(1, 'tags', snapshot['started_at'])
                        progress.check_cancelled()
                    raise CodexError('Original reassignment failure.')
                if index == 1:
                    if self.failure:
                        assert self.release.wait(3)
                        # Let the originating exception set the sibling abort.
                        for _ in range(100):
                            progress.check_cancelled()
                            time.sleep(.001)
                    self.release.set()
                elif index == 0:
                    assert self.release.wait(3)
            finally:
                with self.lock:
                    self.active -= 1
        return json.dumps({'assignments': [{'i': row['i'], 't': [labels['Security']] if followup else []}
                                          for row in payload['records']]})


def run(engine, records, manager):
    token = _marker.set('copied')
    try:
        return topics.group_records(engine, 1, manager, records, compact_assignment=True)
    finally:
        _marker.reset(token)


def test_reviewed_assignments_parallel_bounded_deterministic_and_cacheable(setup):
    engine, records = setup
    original = deepcopy(records)
    manager = Manager()
    result = run(engine, records, manager)
    assert manager.peak == 2
    assert len(manager.transports) == 4
    assert sorted(manager.followup_calls) == [0, 1, 2, 3]
    assert result['topic_groups'][0]['label'] == 'Security'
    assert result['topic_groups'][0]['item_ids'] == ['0', '1', '2', '3']
    assert records == original
    count = len(manager.calls)
    replay = run(engine, records, manager)
    assert replay['topic_groups'] == result['topic_groups']
    assert replay['topic_grouping']['ai_requests'] == 0
    assert len(manager.calls) == count


@pytest.mark.parametrize('failure', ['error', 'cancel'])
def test_reassignment_failure_stops_dispatch_and_does_not_cache_invalid_batches(setup, failure):
    engine, records = setup
    manager = Manager(failure=failure)
    if failure == 'cancel':
        progress.begin(1, 'tags')
    try:
        expected = progress.GenerationCancelled if failure == 'cancel' else CodexError
        with pytest.raises(expected) as error:
            run(engine, records, manager)
        if failure == 'error':
            assert str(error.value) == 'Original reassignment failure.'
        assert sorted(manager.followup_calls) == [0, 1]
        # Bootstrap, four initial negative assignments, and catalog review survive;
        # neither failing reassignment response reaches the cache.
        with Session(engine) as db:
            cached = list(db.scalars(select(ProjectHistoryBatch)))
        assert len(cached) == 6
    finally:
        if failure == 'cancel':
            progress.finish(1, progress.GenerationCancelled())
