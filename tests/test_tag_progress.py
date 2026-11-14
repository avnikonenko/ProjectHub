"""Standalone tag progress with mocked semantic generation (no protocol calls)."""
import json
import threading

import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project
from backend.app.integrations.codex import progress, topic_groups as topics, knowledge


@pytest.fixture
def engine(tmp_path):
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add(Project(name='Tags'))
        db.commit()
    return engine


def records():
    return [{'id': str(i), 'statement': 'SQLite', 'category': 'DECISION',
             'design_status': 'AGREED', 'scope': 'storage'} for i in range(2)]


class Manager:
    def generate_brief(self, prompt, schema):
        payload = json.loads(prompt.rsplit('\n', 1)[1])
        return json.dumps({'groups': [], 'assignments': [
            {'item_id': r['id'], 'group_ids': []} for r in payload['records']]})


def test_tag_batch_progress_cold_warm_and_unchanged(engine, monkeypatch):
    monkeypatch.setattr(topics, 'MAX_BATCH_ITEMS', 1)
    manager = Manager()
    for previous, expected_total, expected_hits in [(None, 2, 0), (None, 2, 2)]:
        progress.begin(1, 'tags')
        try:
            result = topics.group_records(engine, 1, manager, records(), previous=previous)
            state = progress.read(1, 'tags')
            assert state['stage'] == 'Organizing AI tags'
            assert state['batch_phase'] == 'AI tagging'
            assert state['batch_total'] == state['batch_completed'] == expected_total
            assert state['cache_hits'] == expected_hits
            assert state['batch_started_at'] is None
            assert state['last_batch_seconds'] >= 0
            assert len(result['topic_grouping']['batch_timings']) == 2
        finally:
            progress.finish(1)
    progress.begin(1, 'tags')
    try:
        topics.group_records(engine, 1, manager, records(), previous=result)
        assert progress.read(1, 'tags')['batch_total'] == 0
    finally:
        progress.finish(1)


def test_empty_or_reused_run_still_checks_cancelled(engine):
    progress.begin(1, 'tags')
    try:
        state = progress.read(1, 'tags')
        progress.cancel(1, 'tags', state['started_at'])
        with pytest.raises(progress.GenerationCancelled):
            topics.group_records(engine, 1, Manager(), [])
    finally:
        progress.finish(1)


def test_parallel_completion_is_visible_before_first_batch_finishes(engine, monkeypatch):
    monkeypatch.setattr(topics, 'MAX_BATCH_ITEMS', 1)
    monkeypatch.setattr(knowledge, '_extraction_workers', lambda manager: 2)
    completed = threading.Event()
    update = progress.update

    def observe(project_id, **fields):
        update(project_id, **fields)
        if fields.get('batch_completed') == 1:
            completed.set()

    monkeypatch.setattr(progress, 'update', observe)

    class ParallelManager(Manager):
        def generate_brief(self, prompt, schema):
            payload = json.loads(prompt.rsplit('\n', 1)[1])
            if payload['records'][0]['id'] == '0':
                assert completed.wait(3), 'second batch completion was hidden behind first batch'
            return super().generate_brief(prompt, schema)

    progress.begin(1, 'tags')
    try:
        topics.group_records(engine, 1, ParallelManager(), records())
        assert progress.read(1, 'tags')['batch_completed'] == 2
    finally:
        progress.finish(1)


def test_fully_reused_run_still_enforces_budget(engine, monkeypatch):
    manager = Manager()
    previous = topics.group_records(engine, 1, manager, records())
    progress.begin(1, 'tags')
    try:
        clock, budget = progress._clocks[1]
        monkeypatch.setitem(progress._clocks, 1, (clock - budget - 1, budget))
        with pytest.raises(topics.CodexError, match='time budget'):
            topics.group_records(engine, 1, manager, records(), previous=previous)
        assert previous['topic_grouping']['reused_items'] == 0
    finally:
        progress.finish(1)
