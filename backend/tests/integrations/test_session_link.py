"""Session linking changes visibility without discarding project or Codex history."""
import threading

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.app.models import AuditEvent, CodexSession, Delivery, Project


class FakeHistory:
    lock = threading.RLock()

    def __init__(self):
        self.reads = []
        self.released = []

    def release_session(self, session_id):
        self.released.append(session_id)

    def _new_read_client(self):
        return self

    def close(self):
        pass

    def history(self, session, cursor, *, client, archive):
        assert client is self and archive and cursor is None
        self.reads.append(session.id)
        return {'turns': [{'id': 'saved-turn', 'status': 'completed',
                           'messages': [{'role': 'assistant', 'text': 'example evidence'}]}],
                'next_cursor': None}


def test_unlink_hides_session_and_search_but_preserves_citations_and_relinks(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    app = create_app(tmp_path / 'data')
    manager = FakeHistory()
    app.state.codex_manager = manager
    with Session(app.state.engine) as db:
        db.add_all([Project(id=1, name='Example'), Project(id=2, name='Other')])
        db.flush()
        db.add(CodexSession(id=1, project_id=1, thread_id='example-thread', title='Example title'))
        db.flush()
        db.add(Delivery(id=1, project_id=1, session_id=1, content='Saved request', status='queued'))
        db.commit()

    prefix = '/api/projects/1/codex'
    session = f'{prefix}/sessions/1'
    with TestClient(app) as client:
        assert client.post(f'{prefix}/history/index').json()['indexed_turns'] == 1
        assert client.patch('/api/projects/2/codex/sessions/1/link', json={'linked': False}).status_code == 404
        assert manager.released == []
        assert client.patch(f'{session}/link', json={'linked': 'false'}).status_code == 422
        assert client.patch(f'{session}/link', json={'linked': False}).json()['is_linked'] is False
        assert manager.released == [1]
        assert client.get(f'{prefix}/sessions').json() == []
        assert client.get(f'{prefix}/history/search', params={'q': 'evidence'}).json()['indexed_turns'] == 0
        assert client.get(f'/api/projects/1/history/search', params={'q': 'evidence'}).json()['results'] == []
        ask = client.post(f'{prefix}/ask', json={'confirmed': True, 'question': 'evidence'})
        assert ask.status_code == 200 and not ask.json()['sources']
        assert client.post(f'{prefix}/history/index').json()['indexed_turns'] == 0
        assert manager.reads == [1]
        assert client.get(f'{session}/history/turn', params={'turn_id': 'saved-turn'}).status_code == 200
        assert client.get(f'{session}/events').json()['deliveries'][0]['content'] == 'Saved request'
        assert client.get(f'{session}/history').status_code == 409
        assert client.post(f'{session}/resume').status_code == 409
        assert client.post(f'{session}/turns', json={'prompt': 'hello'}).status_code == 409
        assert client.post(f'{prefix}/deliveries/1/send', json={'confirmed': True}).status_code == 409
        request = {'thread_id': 'example-thread', 'title': 'Not a rename'}
        assert client.post('/api/projects/2/codex/sessions', json=request).status_code == 409
        restored = client.post(f'{prefix}/sessions', json=request)
        assert restored.status_code == 201
        assert (restored.json()['id'], restored.json()['title']) == (1, 'Example title')
        assert client.get(f'{prefix}/history/search', params={'q': 'evidence'}).json()['indexed_turns'] == 1
        with Session(app.state.engine) as db:
            row = db.get(CodexSession, 1)
            row.status = 'running'
            db.commit()
        assert client.patch(f'{session}/link', json={'linked': False}).status_code == 409
        assert manager.released == [1]
        with Session(app.state.engine) as db:
            actions = list(db.scalars(select(AuditEvent.action).order_by(AuditEvent.id)))
            assert actions == ['session_unlinked', 'session_relinked']
