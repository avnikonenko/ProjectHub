"""Real indexed SQLite records with exact shared-prefix display/retrieval metadata."""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import CodexSession, Project
from backend.app.integrations.codex.history_duplicates import annotate_page, deduplicate_sources, indexed_sharing


def turn(identifier, value):
    return {'id': identifier, 'status': 'completed', 'messages': [{'role': 'user', 'text': value}]}


@pytest.fixture
def history(tmp_path):
    engine, _ = initialize(tmp_path)
    sequences = {
        1: [turn('a1', 'First context ' * 12), turn('a2', 'Second context ' * 12), turn('a3', 'Original suffix')],
        2: [turn('b1', 'First context ' * 12), turn('b2', 'Second context ' * 12), turn('b3', 'Fork suffix')],
        3: [turn('c1', 'First context ' * 12), turn('c2', 'Second context ' * 12)],
    }
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Foreign')])
        db.flush()
        for sid, turns in sequences.items():
            pid = 2 if sid == 3 else 1
            db.add(CodexSession(id=sid, project_id=pid, thread_id=f'thread-{sid}', title=f'Session {sid}',
                               history_indexed_at='version1', history_indexed_activity='activity1'))
            db.flush()
            for item in turns:
                db.execute(text('INSERT INTO codex_history_turns '
                    '(project_id,session_id,session_title,turn_id,status,messages,content) '
                    'VALUES (:pid,:sid,:title,:tid,:status,:messages,:content)'), {
                        'pid': pid, 'sid': sid, 'title': f'Session {sid}', 'tid': item['id'],
                        'status': item['status'], 'messages': json.dumps(item['messages']),
                        'content': item['messages'][0]['text'],
                    })
        db.commit()
    yield engine, sequences
    engine.dispose()


def test_page_annotations_preserve_original_text_and_reject_changed_live_content(history):
    engine, sequences = history
    page = {'turns': sequences[2], 'next_cursor': 'older'}
    with Session(engine) as db:
        result = annotate_page(db, 1, 2, page)
        assert result['next_cursor'] == 'older'
        for before, after in zip(page['turns'], result['turns']):
            assert all(after[key] == value for key, value in before.items())
        assert result['turns'][0]['shared_history'] == {
            'session_id': 1, 'turn_id': 'a1', 'session_title': 'Session 1',
        }
        assert 'shared_history' not in result['turns'][2]
        assert all('shared_history' not in item for item in page['turns'])
        changed = turn('b1', 'Edited first context')
        assert 'shared_history' not in annotate_page(db, 1, 2, {'turns': [changed]})['turns'][0]
        assert indexed_sharing(db, 2)[0].canonical == {}


def test_retrieved_sources_keep_one_copy_and_resolvable_other_session_links(history):
    engine, _ = history
    sources = [
        {'id': '1', 'session_id': 2, 'turn_id': 'b1', 'text': 'Fork excerpt'},
        {'id': '2', 'session_id': 1, 'turn_id': 'a1', 'text': 'Original excerpt'},
        {'id': '3', 'note_id': 7, 'text': 'An ordinary note'},
    ]
    with Session(engine) as db:
        result = deduplicate_sources(db, 1, sources)
        assert len(result) == 2
        assert result[0]['text'] == 'Fork excerpt'
        assert result[0]['shared_sessions'] == [
            {'session_id': 1, 'turn_id': 'a1', 'session_title': 'Session 1'},
        ]
        assert result[1] == sources[2]
        assert 'shared_sessions' not in sources[0]


def test_unlink_and_changed_index_invalidate_sharing_cache(history):
    engine, _ = history
    with Session(engine) as db:
        assert indexed_sharing(db, 1)[0].canonical
        row = db.get(CodexSession, 1)
        row.is_linked = False
        db.commit()
        assert indexed_sharing(db, 1)[0].canonical == {}
        row.is_linked = True
        db.commit()
        assert indexed_sharing(db, 1)[0].canonical
        db.execute(text("UPDATE codex_history_turns SET messages=:messages WHERE session_id=2 AND turn_id='b1'"),
                   {'messages': json.dumps([{'role': 'user', 'text': 'Changed history'}])})
        db.get(CodexSession, 2).history_indexed_at = 'version2'
        db.commit()
        assert indexed_sharing(db, 1)[0].canonical == {}


def test_incomplete_saved_indexes_do_not_collapse_live_history(history):
    engine, _ = history
    with Session(engine) as db:
        db.get(CodexSession, 2).history_indexed_activity = None
        db.commit()
        assert indexed_sharing(db, 1)[0].canonical == {}


def test_ask_and_scheme_reuse_shared_evidence_without_removing_index_rows(history):
    from backend.app.integrations.codex.ask import retrieve_passages
    from backend.app.integrations.codex.scheme_evidence import _records
    from backend.app.project_evidence import _reported_sources

    engine, _ = history
    with Session(engine) as db:
        asked = retrieve_passages(db, 1, 'First context')
        assert len(asked) == 2
        assert all(source.get('shared_sessions') for source in asked)
        records = _records(db, db.get(Project, 1))[0]
        conversations = [source for source in records if 'session_id' in source]
        assert len(conversations) == 4  # Two shared turns and both distinct suffixes.
        assert len([source for source in conversations if source.get('shared_sessions')]) == 2
        reported = _reported_sources(db, 1, 'First context')
        assert len(reported) == 4
        assert db.execute(text('SELECT count(*) FROM codex_history_turns')).scalar_one() == 8
