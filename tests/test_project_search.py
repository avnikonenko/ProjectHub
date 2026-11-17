"""Real SQLite/FTS and HTTP search tests; Codex is deliberately unavailable."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.app.models import Project, Task, Note, ProjectQA, CodexSession, ProjectKnowledge
from backend.app.integrations.codex.manager import CodexManager


@pytest.fixture
def search_app(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    monkeypatch.setattr(CodexManager, '_new_client', lambda *a, **k: pytest.fail('Search invoked Codex'))
    app = create_app(tmp_path / 'data')
    with Session(app.state.engine) as db:
        db.add_all([Project(id=1, name='Project One'), Project(id=2, name='Project Two')])
        db.commit()
    with TestClient(app) as client:
        yield client, app.state.engine
    app.state.engine.dispose()


def get(client, kind='all', query='ligand', offset=0, pid=1):
    response = client.get(f'/api/projects/{pid}/search/all', params={'q': query, 'kind': kind, 'offset': offset})
    assert response.status_code == 200, response.text
    return {group['kind']: group for group in response.json()['groups']}


def test_search_groups_current_qa_and_completed_tasks_are_isolated(search_app):
    client, engine = search_app
    with Session(engine) as db:
        for pid in (1, 2):
            db.add(Task(project_id=pid, title=f'Ligand task {pid}', description='Evaluate ligand geometry', status='completed'))
            db.add(Note(project_id=pid, title=f'Ligand note {pid}', content='ligand method'))
            db.add(ProjectQA(project_id=pid, generated_at='now', items=[
                {'question': f'Which method {pid}?', 'answer': 'Current method',
                 'variants': [{'answer': 'Use ligand controls'}]}], sources=[]))
            db.add(CodexSession(project_id=pid, title=f'Ligand discussion {pid}', thread_id=f'thread-{pid}', topic='Analysis'))
        db.commit()
    groups = get(client)
    assert [r['title'] for r in groups['tasks']['results']] == ['Ligand task 1']
    assert [r['title'] for r in groups['notes']['results']] == ['Ligand note 1']
    assert [r['title'] for r in groups['qa']['results']] == ['Which method 1?']
    assert 'ligand controls' in groups['qa']['results'][0]['snippet']
    assert [r['title'] for r in groups['sessions']['results']] == ['Ligand discussion 1']
    assert client.get('/api/projects/999/search/all?q=ligand').status_code == 404
    # Existing array endpoint remains compatible.
    assert isinstance(client.get('/api/projects/1/search?q=ligand').json(), list)


def test_conversations_use_existing_index_and_hide_unlinked_or_foreign_history(search_app):
    client, engine = search_app
    with Session(engine) as db:
        for sid, pid, linked in [(1, 1, True), (2, 1, False), (3, 2, True)]:
            db.add(CodexSession(id=sid, project_id=pid, thread_id=f'thread-{sid}', title=f'Session {sid}', is_linked=linked))
        db.flush()
        for sid, pid in [(1, 1), (2, 1), (3, 2)]:
            db.execute(text('INSERT INTO codex_history_turns(project_id,session_id,session_title,turn_id,status,messages,content) '
                            "VALUES(:pid,:sid,'Saved title',:turn,'completed','[]','ligand evidence')"),
                       {'pid': pid, 'sid': sid, 'turn': f'turn-{sid}'})
        db.add(Note(project_id=1, title='Shared ligand discussion', content='ligand plan', kind='document',
                    reference='https://chatgpt.com/share/11111111-1111-1111-1111-111111111111'))
        db.commit()
    groups = get(client)
    rows = groups['conversations']['results']
    assert len(rows) == 2
    assert {r['title'] for r in rows} == {'Session 1', 'Shared ligand discussion'}
    assert all(r['href'].startswith('#history?project=1&') for r in rows)
    assert not groups['notes']['results']
    with Session(engine) as db:
        assert db.execute(text('SELECT COUNT(*) FROM codex_history_turns')).scalar() == 3


def test_bounded_pages_all_terms_and_snippets(search_app):
    client, engine = search_app
    with Session(engine) as db:
        for i in range(23):
            db.add(Note(project_id=1, title=f'Ligand geometry {i}', content='ligand geometry ' + 'x' * 5000))
        db.add(Note(project_id=1, title='Only ligand', content='No second token'))
        db.commit()
    first = get(client, 'notes', 'ligand geometry')['notes']
    assert len(first['results']) == 10
    assert first['next_offset'] == 10
    second = get(client, 'notes', 'ligand geometry', 10)['notes']
    final = get(client, 'notes', 'ligand geometry', 20)['notes']
    assert len(final['results']) == 3 and final['next_offset'] is None
    ids = [r['id'] for page in (first, second, final) for r in page['results']]
    assert len(set(ids)) == 23
    assert all(len(r['snippet']) <= 600 for r in first['results'])
    assert not get(client, 'notes', 'geometry absent')['notes']['results']
    assert not get(client, 'notes', '***')['notes']['results']


@pytest.mark.parametrize('params', [{'kind': 'invalid'}, {'offset': -1}, {'offset': 2147483648}, {'q': 'x' * 2001}])
def test_invalid_search_parameters(search_app, params):
    client, _ = search_app
    assert client.get('/api/projects/1/search/all', params=params).status_code == 422


def test_knowledge_search_includes_evidence_and_current_publication_only(search_app):
    client, engine = search_app
    with Session(engine) as db:
        for pid in (1, 2):
            db.add(ProjectKnowledge(project_id=pid, snapshot={
                'project_id': pid, 'publication_id': f'publication-{pid}',
                'items': [{'id': 'choice', 'statement': f'Conservative method {pid}', 'category': 'design',
                           'scope': 'preparation', 'design_status': 'AGREED',
                           'evidence': [{'source_id': 'a', 'excerpt': 'ligand geometry', 'label': 'Discussion'}]}],
                'sources': [{'id': 'a', 'label': 'Original benchmark'}]}))
        db.commit()
    group = get(client, 'knowledge', 'ligand geometry')['knowledge']
    assert len(group['results']) == 1
    result = group['results'][0]
    assert result['title'] == 'Conservative method 1'
    assert result['href'] == '#knowledge?project=1&item=choice&publication=publication-1'
    assert get(client, 'knowledge', 'benchmark')['knowledge']['results']


def test_json_and_session_search_unicode_casefold(search_app):
    client, engine = search_app
    with Session(engine) as db:
        db.add(ProjectQA(project_id=1, generated_at='now', items=[{'question': 'Ätom model', 'answer': 'Observed'}], sources=[]))
        db.add(CodexSession(project_id=1, title='Ätom discussion', thread_id='unicode-thread'))
        db.commit()
    assert get(client, 'qa', 'ätom')['qa']['results']
    assert get(client, 'sessions', 'ätom')['sessions']['results']


def test_notes_reference_matches_merge_without_duplicates_across_pages(search_app):
    client, engine = search_app
    with Session(engine) as db:
        for i in range(12):
            db.add(Note(project_id=1, title=f'Method {i}', content='ordinary body', reference='https://example.test/ligand'))
        db.add(Note(project_id=1, title='Ligand method', content='ligand body', reference='https://example.test/ligand'))
        db.add(Note(project_id=2, title='Foreign method', content='ordinary body', reference='https://example.test/ligand'))
        db.commit()
    first = get(client, 'notes', 'ligand')['notes']
    second = get(client, 'notes', 'ligand', first['next_offset'])['notes']
    ids = [row['id'] for page in (first, second) for row in page['results']]
    assert len(ids) == len(set(ids)) == 13
    assert second['next_offset'] is None
    mixed = get(client, 'notes', 'method ligand')['notes']
    assert len(mixed['results']) == 10
    assert not get(client, 'notes', 'foreign ligand')['notes']['results']


def test_note_fallback_folds_each_candidate_once(search_app, monkeypatch):
    from backend.app import project_search

    client, engine = search_app
    original = project_search._search_contains
    calls = []

    def measured(value, *terms):
        calls.append(terms)
        return original(value, *terms)

    monkeypatch.setattr(project_search, '_search_contains', measured)
    with Session(engine) as db:
        db.add(Note(project_id=1, title='Method', content='Straße screening', reference='https://example.test/Ligand'))
        db.commit()
    assert get(client, 'notes', 'method STRASSE ligand')['notes']['results']
    assert calls == [('method', 'strasse', 'ligand')]


def test_sessions_match_local_id_account_and_status(search_app):
    client, engine = search_app
    with Session(engine) as db:
        db.add(CodexSession(id=456, project_id=1, title='Preparation', thread_id='stored-thread',
                            codex_account='work-profile', status='linked'))
        db.add(CodexSession(id=457, project_id=2, title='Preparation', thread_id='other-thread',
                            codex_account='private-profile', status='running'))
        db.commit()
    for query in ('456', 'work profile', 'linked', 'preparation profile'):
        result = get(client, 'sessions', query)['sessions']['results']
        assert [row['id'] for row in result] == ['session:456']
    assert not get(client, 'sessions', 'private profile')['sessions']['results']
