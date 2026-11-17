"""Concurrent SQLite refreshes cannot bypass optional sharing metadata bounds."""
import copy
import json
import sqlite3

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import CodexSession, Project
from backend.app.integrations.codex import history_duplicates as duplicates


@pytest.fixture
def history(tmp_path):
    engine, directory = initialize(tmp_path / 'data')
    messages = json.dumps([{'role': 'user', 'text': 'Original shared user evidence. ' * 20}])
    with Session(engine) as db:
        db.add(Project(id=1, name='Isolated project'))
        db.flush()
        db.add_all([CodexSession(id=i, project_id=1, thread_id=f'thread-{i}',
                                history_indexed_at='v1', history_indexed_activity='activity') for i in (1, 2)])
        db.flush()
        for sid in (1, 2):
            for i in (1, 2):
                db.execute(text('INSERT INTO codex_history_turns '
                    '(project_id,session_id,session_title,turn_id,status,messages,content) '
                    "VALUES(1,:sid,'Saved',:turn,'completed',:messages,'Original evidence')"),
                    {'sid': sid, 'turn': f't{sid}-{i}', 'messages': messages})
        db.commit()
    with duplicates._lock:
        duplicates._cache.clear()
    yield engine, directory / 'projecthub.sqlite3', messages
    with duplicates._lock:
        duplicates._cache.clear()
    engine.dispose()


def refresh_before_turns(engine, path, mutate):
    """Commit from a real second connection after the preflight count ran."""
    changed = []
    def before(connection, cursor, statement, parameters, context, many):
        if changed or not statement.startswith('SELECT turn_id,status,messages FROM codex_history_turns'):
            return
        with sqlite3.connect(path, timeout=.5) as writer:
            mutate(writer)
            writer.execute("UPDATE codex_sessions SET history_indexed_at='v2' WHERE id=1")
            writer.commit()
        changed.append(True)
    event.listen(engine, 'before_cursor_execute', before)
    return changed, before


def test_actual_turn_budget_stops_before_decoding_sentinel_after_concurrent_growth(history, monkeypatch):
    engine, path, messages = history
    with engine.begin() as db:
        db.execute(text("DELETE FROM codex_history_turns WHERE turn_id != 't1-1'"))
    monkeypatch.setattr(duplicates, 'MAX_INDEX_TURNS', 1)
    def insert(writer):
        writer.executemany('INSERT INTO codex_history_turns '
            '(project_id,session_id,session_title,turn_id,status,messages,content) '
            "VALUES(1,1,'Saved',?,'completed',?,'Added')",
            [('added-1', 'must-not-decode'), ('added-2', messages)])
    changed, listener = refresh_before_turns(engine, path, insert)
    decoded = []
    loads = duplicates.json.loads
    def traced(value):
        decoded.append(value)
        return loads(value)
    monkeypatch.setattr(duplicates.json, 'loads', traced)
    try:
        with Session(engine) as db:
            sharing, signatures = duplicates.indexed_sharing(db, 1)
    finally:
        event.remove(engine, 'before_cursor_execute', listener)
    assert changed and len(decoded) == 1
    assert not sharing._session_turns and not signatures and not duplicates._cache


def test_actual_identifier_budget_stops_before_decode_when_identifier_grows(history, monkeypatch):
    engine, path, _ = history
    with Session(engine) as db:
        markers = duplicates._markers(db, 1)
    marker_chars = sum(len(str(value)) for marker in markers for value in marker if value is not None)
    monkeypatch.setattr(duplicates, 'MAX_ID_CHARS', marker_chars + 100)
    def update(writer):
        writer.execute("UPDATE codex_history_turns SET turn_id=? WHERE turn_id='t1-1'", ('x' * 1000,))
    changed, listener = refresh_before_turns(engine, path, update)
    def forbidden(value):
        pytest.fail('Oversized metadata was decoded before checking its actual budget')
    monkeypatch.setattr(duplicates.json, 'loads', forbidden)
    try:
        with Session(engine) as db:
            sharing, signatures = duplicates.indexed_sharing(db, 1)
    finally:
        event.remove(engine, 'before_cursor_execute', listener)
    assert changed and not sharing._session_turns and not signatures and not duplicates._cache


@pytest.mark.parametrize('change', ['insert', 'delete', 'message_update', 'unlink'])
def test_index_replacement_is_not_published_under_old_markers_and_sources_stay_complete(history, change):
    engine, path, messages = history
    def replace(writer):
        if change == 'insert':
            writer.execute('INSERT INTO codex_history_turns '
                '(project_id,session_id,session_title,turn_id,status,messages,content) '
                "VALUES(1,1,'Saved','added','completed',?,'Added')", (messages,))
        elif change == 'delete':
            writer.execute("DELETE FROM codex_history_turns WHERE turn_id='t1-2'")
        elif change == 'message_update':
            writer.execute("UPDATE codex_history_turns SET messages=? WHERE turn_id='t1-1'",
                           (json.dumps([{'role': 'user', 'text': 'Revised original evidence'}]),))
        else:
            writer.execute('UPDATE codex_sessions SET is_linked=0 WHERE id=1')
    changed, listener = refresh_before_turns(engine, path, replace)
    sources = [{'session_id': 1, 'turn_id': 't1-1', 'text': 'An exact excerpt'},
               {'session_id': 2, 'turn_id': 't2-1', 'text': 'An exact excerpt'}]
    before = copy.deepcopy(sources)
    try:
        with Session(engine) as db:
            result = duplicates.deduplicate_sources(db, 1, sources)
    finally:
        event.remove(engine, 'before_cursor_execute', listener)
    assert changed and result == before and sources == before
    assert not duplicates._cache
    # A subsequent read can build a coherent cache from the new revision.
    with Session(engine) as db:
        duplicates.indexed_sharing(db, 1)
    assert len(duplicates._cache) == 1
    key = next(iter(duplicates._cache))
    assert all(marker[1] == 'v2' for marker in key[2] if marker[0] == 1)
