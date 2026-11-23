"""Cancellation HTTP contracts use mocked generation; no live Codex requests."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.main import create_app
from backend.app.models import GenerationRevision, Project, ProjectQA
from backend.app.integrations.codex import progress, qa, router as routes
from backend.app.integrations.codex.generation_metrics import begin_model_request


def test_cancel_ask_targets_exact_project_run_and_releases_generation_lock(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    monkeypatch.setattr(routes, 'manager_for', lambda *args, **kwargs: object())
    monkeypatch.setattr(routes, 'retrieve_passages', lambda *args: [{'id': '1', 'text': 'Evidence'}])
    entered, release = Event(), Event()

    def answer(*args):
        usage = begin_model_request('fixture-thread')
        usage.bind_turn('fixture-turn')
        usage.record({'threadId': 'fixture-thread', 'turnId': 'fixture-turn',
                      'tokenUsage': {'total': {'totalTokens': 42}}})
        entered.set()
        while not release.wait(0.01):
            progress.check_cancelled()
        return {'answer': 'Answer', 'sources': []}

    monkeypatch.setattr(routes, 'answer_project', answer)
    try:
        with TestClient(app) as client:
            pid = client.post('/api/projects', json={'name': 'One'}).json()['id']
            other = client.post('/api/projects', json={'name': 'Other'}).json()['id']
            base = f'/api/projects/{pid}/codex'
            request_id = str(uuid4())
            with ThreadPoolExecutor(max_workers=1) as worker:
                pending = worker.submit(client.post, base + '/ask', json={
                    'confirmed': True, 'question': 'Explain it', 'request_id': request_id,
                })
                try:
                    assert entered.wait(3)
                    status = client.get(base + '/generation/status?kind=ask').json()
                    assert status['request_id'] == request_id
                    assert status['live_token_usage'] == {
                        'total_tokens': 42, 'reported_requests': 1, 'model_requests': 1,
                    }
                    other_status = client.get(f'/api/projects/{other}/codex/generation/status?kind=ask').json()
                    assert 'live_token_usage' not in other_status
                    assert 'request_id' not in other_status
                    body = {'kind': 'ask', 'started_at': status['started_at']}
                    assert client.post(f'/api/projects/{other}/codex/generation/cancel', json=body).status_code == 409
                    assert client.post(base + '/generation/cancel', json={**body, 'started_at': 'stale'}).status_code == 409
                    accepted = client.post(base + '/generation/cancel', json=body)
                    assert accepted.status_code == 200
                    assert accepted.json()['cancel_requested'] is True
                    assert accepted.json()['live_token_usage'] == status['live_token_usage']
                    assert pending.result(timeout=3).status_code == 503
                finally:
                    release.set()
            cancelled = client.get(base + '/generation/status?kind=ask').json()
            assert cancelled['state'] == 'cancelled'
            assert cancelled['request_id'] == request_id
            assert 'live_token_usage' not in cancelled
            assert not routes.generation_lock.locked()
            assert client.post(base + '/generation/cancel', json=body).status_code == 409
            assert client.post(base + '/ask', json={'confirmed': True, 'question': 'Try again'}).json()['answer'] == 'Answer'
            assert 'request_id' not in client.get(base + '/generation/status?kind=ask').json()
    finally:
        release.set()
        app.state.engine.dispose()


def test_cancelled_qa_does_not_replace_saved_snapshot_or_revision(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(id=1, name='One'))
        db.flush()
        db.add(ProjectQA(project_id=1, generated_at='old', items=[{'question': 'Old', 'answer': 'Keep'}], sources=[]))
        db.commit()
        previous = qa.read_qa(db, 1)
    monkeypatch.setattr(qa, 'archive_history', lambda *args: ([{
        'text': 'Use local storage', 'sources': [{'id': 'turn:1', 'text': 'Use local storage'}],
    }], []))

    class Generator:
        def generate_brief(self, prompt, schema):
            if prompt.startswith('Select and rank existing project Q&A IDs'):
                current = progress.read(1, 'qa')
                progress.cancel(1, 'qa', current['started_at'])
                return json.dumps({'selected_ids': ['q1']})
            return json.dumps({'items': [{'question': 'Which storage?', 'answer': 'Local', 'source_ids': ['1']}]})

    try:
        with pytest.raises(progress.GenerationCancelled):
            qa.refresh_qa(engine, 1, Generator())
        with Session(engine) as db:
            assert qa.read_qa(db, 1) == previous
            assert list(db.scalars(select(GenerationRevision))) == []
        assert progress.read(1, 'qa')['state'] == 'cancelled'
        assert not routes.generation_lock.locked()
    finally:
        engine.dispose()
