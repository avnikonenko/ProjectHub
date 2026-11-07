"""Real FTS5 indexing and HTTP ownership checks with fake read-only history."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.app.models import Project, CodexSession
from backend.app.integrations.codex.protocol import CodexError


def turn(number, value='searchable evidence', status='completed'):
    return {'id': str(number), 'status': status,
            'messages': [{'role': 'user', 'text': 'Question'}, {'role': 'assistant', 'text': value}]}


class FakeHistory:
    def __init__(self):
        self.turns = {'a': [turn(1)], 'b': [turn(2, 'private foreign evidence')]}
        self.calls, self.opens, self.closes = [], 0, 0
        self.fail_cursor = self.repeat = self.during_read = None

    def _new_read_client(self):
        self.opens += 1
        return self

    def close(self):
        self.closes += 1

    def history(self, session, cursor, *, client, archive):
        assert archive is True and client is self
        self.calls.append((session.thread_id, cursor))
        if self.during_read:
            callback, self.during_read = self.during_read, None
            callback()
        if self.fail_cursor is not None and cursor == self.fail_cursor:
            raise CodexError('Saved history unavailable')
        offset = int(cursor or 0)
        data = self.turns[session.thread_id]
        return {'turns': data[offset:offset + 5], 'next_cursor':
                self.repeat or (str(offset + 5) if offset + 5 < len(data) else None)}


@pytest.fixture
def env(tmp_path):
    app = create_app(tmp_path / 'data')
    manager = FakeHistory()
    app.state.codex_manager = manager
    with Session(app.state.engine) as db:
        db.add_all([Project(id=1, name='A'), Project(id=2, name='B')]); db.flush()
        db.add_all([CodexSession(id=1, project_id=1, thread_id='a', title='First session'),
                    CodexSession(id=2, project_id=2, thread_id='b', title='Private session')]); db.commit()
    with TestClient(app) as client:
        yield client, manager, app.state.engine


def test_full_index_pagination_scoping_and_exact_turn(env):
    client, manager, _ = env
    manager.turns['a'] = [turn(i, 'needle ' + 'tail ' * 100) for i in range(27)] + [turn(28, 'active secret', 'inProgress')]
    base = '/api/projects/1/codex/history'
    assert client.get(base + '/search').json() == {
        'results': [], 'next_offset': None, 'indexed_at': None, 'indexed_turns': 0}
    indexed = client.post(base + '/index')
    assert indexed.status_code == 200, indexed.text
    assert indexed.json()['indexed_turns'] == 27
    assert manager.calls == [('a', None), ('a', '5'), ('a', '10'), ('a', '15'), ('a', '20'), ('a', '25')]
    assert manager.opens == manager.closes == 1
    first = client.get(base + '/search', params={'q': 'needle'}).json()
    second = client.get(base + '/search', params={'q': 'needle', 'offset': first['next_offset']}).json()
    assert len(first['results']) == 20 and len(second['results']) == 7 and second['next_offset'] is None
    assert len({r['turn_id'] for r in first['results'] + second['results']}) == 27
    assert all(r['session_id'] == 1 and r['session_title'] == 'First session' for r in first['results'])
    assert '[needle]' in first['results'][0]['snippet']
    assert client.get(base + '/search', params={'q': 'active'}).json()['results'] == []
    found = client.get('/api/projects/1/codex/sessions/1/history/turn', params={'turn_id': '26'})
    assert found.json() == manager.turns['a'][26]
    assert client.get('/api/projects/2/codex/sessions/1/history/turn', params={'turn_id': '26'}).status_code == 404
    assert client.get('/api/projects/1/codex/sessions/2/history/turn', params={'turn_id': '2'}).status_code == 404
    assert client.get('/api/projects/1/codex/sessions/1/history/turn', params={'turn_id': '28'}).status_code == 404
    assert client.post('/api/projects/2/codex/history/index').status_code == 200
    assert client.get(base + '/search', params={'q': 'private'}).json()['results'] == []
    assert client.get('/api/projects/2/codex/history/search', params={'q': 'needle'}).json()['results'] == []
    assert client.get(base + '/search').json()['indexed_turns'] == 27


def test_failed_refresh_preserves_index_and_success_replaces_fts(env):
    client, manager, _ = env
    base = '/api/projects/1/codex/history'
    original = client.post(base + '/index').json()
    manager.turns['a'] = [turn(i, 'replacement') for i in range(8)]
    manager.fail_cursor = '5'
    failed = client.post(base + '/index')
    assert failed.status_code == 503 and 'unavailable' in failed.json()['detail']
    state = client.get(base + '/search', params={'q': 'searchable'}).json()
    assert state['indexed_at'] == original['indexed_at'] and state['indexed_turns'] == 1
    assert len(state['results']) == 1 and manager.closes == 2
    manager.fail_cursor = None
    assert client.post(base + '/index').json()['indexed_turns'] == 8
    assert client.get(base + '/search', params={'q': 'searchable'}).json()['results'] == []
    assert len(client.get(base + '/search', params={'q': 'replacement'}).json()['results']) == 8


def test_safe_queries_offsets_and_missing_project(env):
    client, manager, _ = env
    base = '/api/projects/1/codex/history'
    client.post(base + '/index')
    for query in ['" OR * (x)', '---', 'NEAR(a b)', "'; DROP TABLE projects; --"]:
        assert client.get(base + '/search', params={'q': query}).status_code == 200
    for offset in [-1, 10 ** 40]:
        assert client.get(base + '/search', params={'q': 'evidence', 'offset': offset}).status_code == 422
    before = manager.opens
    assert client.post('/api/projects/999/codex/history/index').status_code == 404
    assert client.get('/api/projects/999/codex/history/search').status_code == 404
    assert manager.opens == before


def test_session_change_during_read_preserves_previous_index(env):
    client, manager, engine = env
    base = '/api/projects/1/codex/history'
    original = client.post(base + '/index').json()
    def add_session():
        with Session(engine) as db:
            db.add(CodexSession(project_id=1, thread_id='new')); db.commit()
    manager.during_read = add_session
    failed = client.post(base + '/index')
    assert failed.status_code == 503 and 'associations changed' in failed.json()['detail']
    assert client.get(base + '/search').json()['indexed_at'] == original['indexed_at']


def test_pagination_cycle_is_explicit_and_old_index_survives(env):
    client, manager, _ = env
    base = '/api/projects/1/codex/history'
    original = client.post(base + '/index').json()
    manager.repeat = '0'
    assert client.post(base + '/index').status_code == 503
    assert manager.closes == 2
    assert client.get(base + '/search').json()['indexed_at'] == original['indexed_at']


def test_write_failure_rolls_back_index_replacement(env):
    client, manager, engine = env
    base = '/api/projects/1/codex/history'
    original = client.post(base + '/index').json()
    with engine.begin() as db:
        db.execute(text("CREATE TRIGGER reject_history BEFORE INSERT ON codex_history_turns "
                        "WHEN new.turn_id='fail' BEGIN SELECT RAISE(ABORT,'simulated write failure'); END"))
    manager.turns['a'] = [turn('new', 'replacement'), turn('fail')]
    with pytest.raises(Exception, match='simulated write failure'):
        client.post(base + '/index')
    state = client.get(base + '/search', params={'q': 'searchable'}).json()
    assert state['indexed_at'] == original['indexed_at'] and len(state['results']) == 1


def test_unified_history_search_and_pagination(env):
    client, manager, _ = env
    base = '/api/projects/1'
    url = 'https://chatgpt.com/share/01234567-89ab-cdef-0123-456789abcdef'

    def note(project, reference, kind='document'):
        response = client.post(f'/api/projects/{project}/notes', json={
            'title': 'Shared chat', 'content': 'User:\nWhere is the needle?',
            'reference': reference, 'kind': kind,
        })
        assert response.status_code == 201
        return response.json()['id']

    shared = note(1, url)
    note(2, url)
    note(1, url, 'note')
    note(1, url[:-1] + 'z')
    query = {'q': 'needle'}
    before = client.get(base + '/history/search', params=query).json()
    assert before['indexed_at'] is None and before['next_offset'] is None
    assert before['results'] == [{
        'kind': 'chatgpt', 'note_id': shared, 'title': 'Shared chat',
        'snippet': 'User:\nWhere is the [needle]?',
    }]

    manager.turns['a'] = [turn(i, 'needle') for i in range(25)]
    assert client.post(base + '/codex/history/index').status_code == 200
    first = client.get(base + '/history/search', params=query).json()
    second = client.get(base + '/history/search', params={**query, 'offset': first['next_offset']}).json()
    assert len(first['results']) == 20 and len(second['results']) == 6
    assert second['next_offset'] is None and first['indexed_turns'] == 25
    results = first['results'] + second['results']
    assert [r['note_id'] for r in results if r['kind'] == 'chatgpt'] == [shared]
    assert {r['turn_id'] for r in results if r['kind'] == 'codex'} == {str(i) for i in range(25)}

    # Imported conversations update through the existing notes FTS triggers.
    assert client.patch(f'{base}/notes/{shared}', json={'content': 'Fresh transcript'}).status_code == 200
    updated = client.get(base + '/history/search', params={'q': 'fresh'}).json()['results']
    assert [r['note_id'] for r in updated] == [shared]
    assert client.get('/api/projects/999/history/search', params=query).status_code == 404
    assert client.get(base + '/history/search', params={**query, 'offset': -1}).status_code == 422
