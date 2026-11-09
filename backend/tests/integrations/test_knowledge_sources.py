"""Primary source fidelity, contextual batching and no remote history access."""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import CodexSession, Note, Project
from backend.app.integrations.codex import knowledge_sources as module
from backend.app.integrations.codex.protocol import CodexError

URL = 'https://chatgpt.com/share/00000000-0000-4000-8000-000000000001'


def setup(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(name='One'), Project(name='Two')])
        db.flush()
        db.add_all([CodexSession(id=1, project_id=1, thread_id='one'),
                    CodexSession(id=2, project_id=2, thread_id='two')])
        db.commit()
    return engine


def insert_turn(db, session=1, project=1, turn='original', messages=None):
    messages = messages or [{'role': 'assistant', 'text': 'Use a local database?'},
                            {'role': 'user', 'text': 'Yes, use SQLite.'}]
    db.execute(text('INSERT INTO codex_history_turns '
        '(project_id,session_id,session_title,turn_id,status,messages,content) '
        'VALUES (:pid,:sid,\'Session\',:turn,\'completed\',:messages,\'flattened search text\')'),
        {'pid': project, 'sid': session, 'turn': turn, 'messages': json.dumps(messages)})


def test_indexed_sources_preserve_roles_and_identity_across_reindex(tmp_path):
    engine = setup(tmp_path)
    with Session(engine) as db:
        insert_turn(db)
        insert_turn(db, session=2, project=2, messages=[{'role': 'user', 'text': 'FOREIGN'}])
        db.commit()
    first = module.load_sources(engine, 1)
    assert [s['role'] for s in first['sources']] == ['assistant', 'user']
    assert first['sources'][1]['text'] == 'Yes, use SQLite.'
    assert 'FOREIGN' not in json.dumps(first)
    assert all(s['timestamp'] is None for s in first['sources'])
    with Session(engine) as db:
        db.execute(text('DELETE FROM codex_history_turns WHERE project_id=1'))
        insert_turn(db)
        db.commit()
    second = module.load_sources(engine, 1)
    assert [(s['id'], s['revision']) for s in second['sources']] == [(s['id'], s['revision']) for s in first['sources']]
    with pytest.raises(CodexError, match='not found'):
        module.load_sources(engine, 999)
    engine.dispose()


def test_imported_messages_have_exact_offsets_and_ignore_fenced_role_markers(tmp_path):
    engine = setup(tmp_path)
    transcript = 'User:\nUse café data.\n\nChatGPT:\nExample:\n```text\nUser:\nquoted marker\n```\nChoose SQLite?\n\nUser:\nAgree.\n'
    with Session(engine) as db:
        db.add_all([Note(project_id=1, title='Original', kind='document', reference=URL, content=transcript),
                    Note(project_id=1, title='AI claim', kind='ai_note', content='We agreed everything'),
                    Note(project_id=2, title='Other', kind='document', reference=URL, content='FOREIGN')])
        db.commit()
    bundle = module.load_sources(engine, 1)
    sources = [s for s in bundle['sources'] if s.get('note_id')]
    assert [s['role'] for s in sources] == ['user', 'assistant', 'user']
    for source in sources:
        assert transcript[source['offset_start']:source['offset_end']] == source['text']
    assert 'We agreed everything' not in json.dumps(bundle)
    assert all(s['timestamp'] is None for s in sources)
    engine.dispose()


def test_changed_message_and_unavailable_or_unlinked_parent_are_distinct(tmp_path):
    engine = setup(tmp_path)
    with Session(engine) as db:
        db.add(Note(project_id=1, title='Original', kind='document', reference=URL,
                    content='User:\nUse SQLite.\nChatGPT:\nUnderstood.'))
        insert_turn(db)
        db.commit()
    before = module.load_sources(engine, 1)
    with Session(engine) as db:
        db.get(Note, 1).content = 'User:\nUse PostgreSQL.\nChatGPT:\nUnderstood.'
        db.get(CodexSession, 1).is_linked = False
        db.commit()
    changed = module.load_sources(engine, 1, before['manifest'])
    a = next(s for s in before['sources'] if s.get('note_id') and s['role'] == 'user')
    b = next(s for s in changed['sources'] if s.get('note_id') and s['role'] == 'user')
    assert a['id'] == b['id'] and a['revision'] != b['revision']
    assert changed['coverage']['excluded'] == ['codex:1:one']
    with Session(engine) as db:
        db.delete(db.get(Note, 1))
        db.commit()
    missing = module.load_sources(engine, 1, changed['manifest'])
    entry = next(e for e in missing['manifest']['entries'] if e.get('note_id') == 1)
    assert entry['availability'] == 'unavailable' and entry['reason'] == 'missing_local_record'
    assert any('remote deletion' in value for value in missing['coverage']['limitations'])
    engine.dispose()


def test_ambiguous_transcript_is_not_promoted_to_user_evidence(tmp_path):
    engine = setup(tmp_path)
    with Session(engine) as db:
        db.add(Note(project_id=1, title='Broken', kind='document', reference=URL,
                    content='User:\nOne\nUser:\nSecond unexplained boundary'))
        db.commit()
    bundle = module.load_sources(engine, 1)
    assert not bundle['sources']
    assert 'chatgpt:note:1' in bundle['coverage']['unavailable']
    engine.dispose()


def test_window_boundary_keeps_previous_proposal_as_context_only():
    sources = [dict(id=str(i), conversation_id='same', role='assistant' if i < 3 else 'user',
                    text=('context\n' * 750 if i < 3 else 'Yes, adopt that option.'), revision=str(i)) for i in range(4)]
    windows = module.build_windows({'sources': sources}, max_chars=15000)
    assert len(windows) >= 2
    assert windows[1]['context_ids'] == [windows[0]['primary_ids'][-1]]
    assert all(len(json.dumps(window, ensure_ascii=False)) <= 15000 for window in windows)
    assert [sid for window in windows for sid in window['primary_ids']] == [s['id'] for s in sources]
    distinct = module.build_windows({'sources': [sources[0], {**sources[3], 'conversation_id': 'other'}]})
    assert not distinct[1]['context_ids']


def test_append_does_not_invalidate_closed_window_or_old_message_hashes(tmp_path):
    engine = setup(tmp_path)
    with Session(engine) as db:
        insert_turn(db, messages=[{'role': 'assistant', 'text': 'Proposal\n' * 2400}, {'role': 'user', 'text': 'Yes.'}])
        db.commit()
    before = module.load_sources(engine, 1)
    first = module.build_windows(before)
    with Session(engine) as db:
        insert_turn(db, turn='later', messages=[{'role': 'user', 'text': 'Another question.'}])
        db.commit()
    after = module.load_sources(engine, 1)
    second = module.build_windows(after)
    assert before['manifest']['fingerprint'] != after['manifest']['fingerprint']
    assert first[0]['fingerprint'] == second[0]['fingerprint']
    assert [(s['id'], s['revision']) for s in before['sources']] == [(s['id'], s['revision']) for s in after['sources'][:-1]]
    engine.dispose()


def test_metadata_changes_invalidate_manifest_and_omissions_are_not_deletion(tmp_path, monkeypatch):
    engine = setup(tmp_path)
    with Session(engine) as db:
        db.add_all([Note(project_id=1, title='One', kind='document', reference=URL,
                         content='User:\nChoose SQLite.\nChatGPT:\nUnderstood.'),
                    Note(project_id=1, title='Two', kind='document', reference=URL,
                         content='User:\nKeep rationale.\nChatGPT:\nUnderstood.')])
        db.commit()
    first = module.load_sources(engine, 1)
    with Session(engine) as db:
        db.get(Project, 1).goals = 'Updated purpose'
        db.commit()
    changed = module.load_sources(engine, 1)
    assert first['manifest']['fingerprint'] != changed['manifest']['fingerprint']
    assert first['sources'] == changed['sources']
    monkeypatch.setattr(module, 'MAX_PARENTS', 1)
    bounded = module.load_sources(engine, 1, first['manifest'])
    second = next(e for e in bounded['manifest']['entries'] if e.get('note_id') == 2)
    assert second['availability'] == 'excluded'
    assert second['reason'] == 'not_selected_as_primary_source'
    engine.dispose()
