"""Server-owned Scheme follow-up starts only after saved Knowledge; mocked AI."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import generation_attempts as attempts, progress, router as routes, scheme
from backend.app.integrations.codex.ai_instructions import ai_instructions
from backend.app.integrations.codex.generation_metrics import begin_model_request
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import GenerationRevision, Project, ProjectAIInstructions, ProjectKnowledge, ProjectScheme, utcnow


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    old = {'project_id': 1, 'publication_id': 'old', 'generated_at': 'old', 'items': []}
    old_scheme = {'generated_at': 'old', 'marker': 'saved Scheme'}
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')]); db.flush()
        db.add_all([ProjectKnowledge(project_id=1, snapshot=old),
                    ProjectScheme(project_id=1, snapshot=old_scheme),
                    ProjectAIInstructions(project_id=1, instructions='Project one writing preference.', replaces_shared=True)])
        db.commit()
    calls, choices = [], []
    class Manager:
        def for_generation(self, model, effort):
            selected = SimpleNamespace(model=model, effort=effort, account_key='fixture-profile')
            choices.append(selected)
            return selected
    manager = Manager()
    monkeypatch.setattr(routes, 'manager_for', lambda app, pid: manager)

    def publish(actual_engine, pid, selected, *, publish):
        assert actual_engine is engine and pid == 1 and selected is choices[0]
        assert 'Project one writing preference.' in ai_instructions()
        calls.append(('knowledge', publish))
        publication = 'new-knowledge'
        brief = {'generated_at': 'new', 'project_state': {'publication_id': publication}}
        saved = {'project_id': pid, 'publication_id': publication, 'generated_at': 'new', 'summary': brief, 'items': []}
        if publish:
            with Session(engine) as db:
                db.get(ProjectKnowledge, pid).snapshot = saved
                db.add(GenerationRevision(project_id=pid, kind='knowledge', generated_at='new', snapshot=saved))
                db.commit()
        return {'published': publish, 'publication_id': publication, 'knowledge': saved, 'brief': brief}

    def generate_scheme(actual_engine, pid, selected, *, expected_publication_id, focus, dependency_token):
        assert actual_engine is engine and pid == 1 and selected is choices[1]
        assert selected is not choices[0] and selected.account_key == choices[0].account_key
        assert selected.model == choices[0].model and selected.effort == choices[0].effort
        assert 'Project one writing preference.' in ai_instructions()
        with Session(engine) as db:
            queued = attempts.read_latest(db, pid, 'scheme')
            assert queued['state'] == 'waiting' and queued['started_at'] == dependency_token
            saved = db.get(ProjectKnowledge, pid).snapshot
            assert saved['publication_id'] == expected_publication_id == 'new-knowledge'
            result = {'generated_at': 'new-scheme', 'knowledge_publication': saved['publication_id'], 'focus': focus}
            db.get(ProjectScheme, pid).snapshot = result
            db.commit()
        calls.append(('scheme', saved['publication_id']))
    monkeypatch.setattr(routes, 'generate_knowledge_brief', publish)
    monkeypatch.setattr(routes, 'refresh_scheme', generate_scheme)
    app = FastAPI(); app.state.engine = engine; app.include_router(routes.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, engine=engine, calls=calls, choices=choices,
                              old=deepcopy(old), old_scheme=deepcopy(old_scheme))
    engine.dispose()


def body(**changes):
    return {'confirmed': True, 'publish': True, 'generate_scheme': True, 'model': 'chosen', 'effort': 'medium', **changes}


def _reported_request(name):
    request = begin_model_request(name)
    request.bind_turn('test-turn')
    return lambda count: request.record({'threadId': name, 'turnId': 'test-turn',
                                         'tokenUsage': {'total': {'totalTokens': count}}})


def test_parallel_routes_share_one_allowance_and_preserve_the_saved_scheme(api, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_GENERATION_TOKEN_LIMIT', '100')
    action = str(uuid4())
    both_active = Barrier(2)
    brief_reported, limit_reached = Event(), Event()

    def run(engine, pid, kind):
        progress.begin(pid, kind, engine=engine)
        try:
            report = _reported_request(kind)
            both_active.wait(5)
            if kind == 'brief':
                report(55)
                brief_reported.set()
                assert limit_reached.wait(5)
            else:
                assert brief_reported.wait(5)
                report(52)
                limit_reached.set()
            progress.complete(pid, lambda: pytest.fail('An unfinished result was saved after reaching the shared limit'))
        except CodexError as exc:
            progress.finish(pid, error=str(exc))
            raise
        finally:
            progress.finish(pid)

    monkeypatch.setattr(routes, 'refresh_brief', lambda engine, pid, *_: run(engine, pid, 'brief'))
    monkeypatch.setattr(routes, 'refresh_scheme', lambda engine, pid, *_, **__: run(engine, pid, 'scheme'))
    prefix = '/api/projects/1/codex'
    payload = {'confirmed': True, 'generation_action_id': action}
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(api.client.post, f'{prefix}/{kind}', json=payload) for kind in ('brief', 'scheme')]
        for pending in requests:
            response = pending.result(timeout=10)
            assert response.status_code == 503
            assert '100 reported tokens' in response.json()['detail']
    assert api.client.get(f'{prefix}/generation/actions/{action}').json() == {
        'limit': 100, 'total_tokens': 107, 'model_requests': 2, 'reported_requests': 2,
        'reached': True, 'active_jobs': 0,
    }
    assert api.client.get(f'/api/projects/2/codex/generation/actions/{action}').status_code == 404
    with Session(api.engine) as db:
        assert db.get(ProjectScheme, 1).snapshot == api.old_scheme
        assert attempts.read_latest(db, 1, 'scheme')['state'] == 'failed'


def test_queued_scheme_keeps_the_knowledge_allowance(api, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_GENERATION_TOKEN_LIMIT', '100')
    original_publish = routes.generate_knowledge_brief
    action = str(uuid4())

    def publish(engine, pid, selected, *, publish):
        progress.begin(pid, 'knowledge', engine=engine)
        try:
            _reported_request('knowledge')(60)
            return progress.complete(pid, lambda: original_publish(engine, pid, selected, publish=publish))
        finally:
            progress.finish(pid)

    def followup(engine, pid, *_args, **_kwargs):
        progress.begin(pid, 'scheme', engine=engine)
        try:
            assert progress.current_metrics().live_usage()['model_requests'] == 0
            _reported_request('scheme')(40)
            progress.complete(pid, lambda: pytest.fail('Scheme exceeded the shared limit'))
        except CodexError as exc:
            progress.finish(pid, error=str(exc))
            raise
        finally:
            progress.finish(pid)

    monkeypatch.setattr(routes, 'generate_knowledge_brief', publish)
    monkeypatch.setattr(routes, 'refresh_scheme', followup)
    prefix = '/api/projects/1/codex'
    response = api.client.post(f'{prefix}/knowledge/brief', json=body(generation_action_id=action))
    assert response.status_code == 200 and response.json()['scheme_requested'] is True
    assert api.client.get(f'{prefix}/generation/actions/{action}').json()['total_tokens'] == 100
    with Session(api.engine) as db:
        assert db.get(ProjectKnowledge, 1).snapshot['publication_id'] == 'new-knowledge'
        assert db.get(ProjectScheme, 1).snapshot == api.old_scheme
        assert '100 reported tokens' in attempts.read_latest(db, 1, 'scheme')['error']


def test_scheme_followup_uses_committed_knowledge_and_a_fresh_manager(api):
    response = api.client.post('/api/projects/1/codex/knowledge/brief', json=body(scheme_focus='code'))
    assert response.status_code == 200, response.text
    assert response.json()['published'] is True and response.json()['scheme_requested'] is True
    assert api.calls == [('knowledge', True), ('scheme', 'new-knowledge')]
    assert len(api.choices) == 2 and api.choices[0] is not api.choices[1]
    with Session(api.engine) as db:
        assert db.get(ProjectScheme, 1).snapshot['knowledge_publication'] == response.json()['publication_id']
        assert db.get(ProjectScheme, 1).snapshot['focus'] == 'code'
        assert db.get(ProjectScheme, 2) is None and db.get(ProjectKnowledge, 2) is None


def test_scheme_followup_is_explicit(api):
    response = api.client.post('/api/projects/1/codex/knowledge/brief', json=body(generate_scheme=False))
    assert response.status_code == 200 and 'scheme_requested' not in response.json()
    assert api.calls == [('knowledge', True)] and len(api.choices) == 1


@pytest.mark.parametrize('legacy_publish', [None, False])
def test_scheme_followup_uses_autosaved_knowledge_even_for_legacy_save_flag(api, legacy_publish):
    payload = body(publish=legacy_publish)
    if legacy_publish is None:
        payload.pop('publish')
    response = api.client.post('/api/projects/1/codex/knowledge/brief', json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['published'] is True
    assert api.calls == [('knowledge', True), ('scheme', 'new-knowledge')]
    before = list(api.calls)
    response = api.client.post('/api/projects/1/codex/analysis/preview', json=body())
    assert response.status_code == 422 and api.calls == before
    assert api.client.post('/api/projects/999/codex/knowledge/brief', json=body()).status_code == 404


@pytest.mark.parametrize('error', [CodexError('Knowledge timed out'), progress.GenerationCancelled()])
def test_failed_or_cancelled_knowledge_never_schedules_scheme(api, monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(routes, 'generate_knowledge_brief', fail)
    response = api.client.post('/api/projects/1/codex/knowledge/brief', json=body())
    assert response.status_code == 503 and api.calls == []
    with Session(api.engine) as db:
        assert db.get(ProjectKnowledge, 1).snapshot == api.old
        assert db.get(ProjectScheme, 1).snapshot == api.old_scheme


def test_scheme_setup_failure_is_detected_before_knowledge_publication(api, monkeypatch):
    def select(model, effort):
        if api.choices:
            raise CodexError('The selected account is unavailable.')
        chosen = object(); api.choices.append(chosen); return chosen
    monkeypatch.setattr(routes, 'manager_for', lambda *args: SimpleNamespace(for_generation=select))
    response = api.client.post('/api/projects/1/codex/knowledge/brief', json=body())
    assert response.status_code == 503 and api.calls == []
    with Session(api.engine) as db:
        assert db.get(ProjectKnowledge, 1).snapshot == api.old


def test_pre_start_scheme_error_keeps_knowledge_saved_and_reports_scheme_failure(api, monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise CodexError('Scheme follow-up could not inspect its sources.')
    monkeypatch.setattr(routes, 'refresh_scheme', fail)
    response = api.client.post('/api/projects/1/codex/knowledge/brief', json=body(scheme_focus='sessions'))
    assert response.status_code == 200 and response.json()['published'] is True
    status = api.client.get('/api/projects/1/codex/generation/status?kind=scheme').json()
    assert status['state'] == 'failed' and 'could not inspect' in status['error']
    assert status['latest_attempt']['settings']['scheme_focus'] == 'sessions'
    assert 'Scheme follow-up failed for project=1' in caplog.text
    with Session(api.engine) as db:
        assert db.get(ProjectKnowledge, 1).snapshot['publication_id'] == 'new-knowledge'
        assert db.get(ProjectScheme, 1).snapshot == api.old_scheme


def test_busy_scheme_followup_does_not_overwrite_another_scheme_run(api, monkeypatch, caplog):
    monkeypatch.setattr(routes, 'refresh_scheme', scheme.refresh_scheme)
    lease = routes.generation_lock.for_job(1, 'scheme')
    assert lease.acquire(blocking=False)
    progress.begin(1, 'scheme')
    status = progress.read(1, 'scheme')
    try:
        response = api.client.post('/api/projects/1/codex/knowledge/brief', json=body())
        assert response.status_code == 200 and response.json()['scheme_requested'] is True
        assert progress.read(1, 'scheme')['started_at'] == status['started_at']
        assert progress.read(1, 'scheme')['state'] == 'running'
        assert progress.read(1, 'scheme').get('cancel_requested') is not True
        assert 'Scheme follow-up not started for project=1' in caplog.text
    finally:
        progress.finish(1)
        lease.release()


def test_current_queue_owns_callback_and_cannot_overwrite_a_newer_saved_scheme(api, monkeypatch):
    queued = []
    publications = iter(('knowledge-a', 'knowledge-b'))

    def defer(_tasks, callback, *args, **kwargs):
        queued.append((callback, args, kwargs))

    def publish(engine, pid, selected, *, publish):
        assert publish and engine is api.engine and pid == 1
        publication = next(publications)
        with Session(engine) as db:
            db.get(ProjectKnowledge, pid).snapshot = {'publication_id': publication}
            db.commit()
        return {'published': True, 'publication_id': publication}

    monkeypatch.setattr(BackgroundTasks, 'add_task', defer)
    monkeypatch.setattr(routes, 'generate_knowledge_brief', publish)
    monkeypatch.setattr(routes, 'refresh_scheme', scheme.refresh_scheme)
    monkeypatch.setattr(scheme, 'collect_evidence', lambda *_: pytest.fail('Stale callback inspected project evidence.'))
    first = api.client.post('/api/projects/1/codex/knowledge/brief', json=body(scheme_focus='code'))
    assert first.status_code == 200 and first.json()['scheme_requested'] is True
    with Session(api.engine) as db:
        waiting = attempts.read_latest(db, 1, 'scheme')
    assert waiting['state'] == 'waiting' and waiting['settings']['scheme_focus'] == 'code'
    second = api.client.post('/api/projects/1/codex/knowledge/brief', json=body(scheme_focus='sessions'))
    assert second.status_code == 200 and second.json()['scheme_requested'] is False
    assert len(queued) == 1
    with Session(api.engine) as db:
        assert attempts.read_latest(db, 1, 'scheme') == waiting
        assert db.get(ProjectKnowledge, 1).snapshot['publication_id'] == 'knowledge-b'

    # A successful manual Scheme superseded the waiting token before its
    # background callback started. It must stay the latest result and attempt.
    newer = utcnow()
    attempts.start(api.engine, 1, 'scheme', newer, settings={'scheme_focus': 'sessions'})
    expected = {'generated_at': newer, 'knowledge_publication': 'knowledge-b'}
    with Session(api.engine) as db:
        db.get(ProjectScheme, 1).snapshot = expected
        attempts.finish(api.engine, 1, 'scheme', newer, state='completed', db=db)
        db.commit()
    callback, args, kwargs = queued.pop()
    callback(*args, **kwargs)
    with Session(api.engine) as db:
        assert db.get(ProjectScheme, 1).snapshot == expected
        status = attempts.read_latest(db, 1, 'scheme')
        assert status['state'] == 'completed' and status['started_at'] == newer
        assert status['settings']['scheme_focus'] == 'sessions'
