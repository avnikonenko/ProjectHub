"""Real save transactions and production transport scope; model/close mocked."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from backend.app.integrations.codex import knowledge_brief, manager as transports, progress, router as routes
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import ProjectKnowledge
from backend.tests.integrations.test_knowledge_brief import prepared, FakeSynthesis, _stored


class CloseFailure:
    calls = 0

    def close(self):
        self.calls += 1
        raise OSError('Simulated transport descriptor cleanup failure')


class CleanupSynthesis(FakeSynthesis):
    generation_session = transports.CodexManager.generation_session

    def __init__(self):
        self.client = CloseFailure()

    def generate_brief(self, prompt, schema):
        scope = transports._generation_transport.get()
        assert scope['owner'] is self
        scope['client'] = self.client
        return super().generate_brief(prompt, schema)


def test_transport_cleanup_failure_does_not_change_committed_autosave(prepared, caplog):
    manager = CleanupSynthesis()
    before = _stored(prepared.engine)
    result = knowledge_brief.generate_knowledge_brief(prepared.engine, 1, manager)
    assert result['published'] is True
    assert _stored(prepared.engine)[0] == result['knowledge']
    assert result['publication_id'] != before[0]['publication_id']
    assert progress.read(1, 'knowledge')['state'] == 'completed'
    assert manager.client.calls == 1
    assert transports._generation_transport.get() is None
    assert 'Could not close generation transport' in caplog.text


def test_transport_cleanup_failure_does_not_replace_original_model_error(prepared, caplog):
    manager = CleanupSynthesis()
    manager.fail = True
    before = _stored(prepared.engine)
    with pytest.raises(CodexError, match='Mock synthesis unavailable'):
        knowledge_brief.generate_knowledge_brief(prepared.engine, 1, manager)
    assert _stored(prepared.engine) == before
    state = progress.read(1, 'knowledge')
    assert state['state'] == 'failed' and state['error'] == 'Mock synthesis unavailable'
    assert manager.client.calls == 1
    assert transports._generation_transport.get() is None
    assert 'Could not close generation transport' in caplog.text


def test_cleanup_failure_keeps_api_success_and_scheme_followup(prepared, monkeypatch):
    manager, scheme_manager = CleanupSynthesis(), object()
    selections = iter((manager, scheme_manager))
    monkeypatch.setattr(routes, 'manager_for', lambda *args: SimpleNamespace(
        for_generation=lambda model, effort: next(selections)))
    seen = []

    def generate_scheme(engine, pid, selected, *, expected_publication_id):
        assert engine is prepared.engine and pid == 1 and selected is scheme_manager
        with Session(engine) as db:
            saved = db.get(ProjectKnowledge, pid).snapshot
            assert saved['publication_id'] != 'old'
            assert expected_publication_id == saved['publication_id']
            seen.append(saved['publication_id'])

    monkeypatch.setattr(routes, 'refresh_scheme', generate_scheme)
    app = FastAPI()
    app.state.engine = prepared.engine
    app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.post('/api/projects/1/codex/knowledge/brief', json={
            'confirmed': True, 'generate_scheme': True})
    assert response.status_code == 200, response.text
    assert response.json()['published'] is True and response.json()['scheme_requested'] is True
    assert seen == [response.json()['publication_id']]
    assert manager.client.calls == 1
