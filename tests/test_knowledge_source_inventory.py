"""Registered-source denominators and omission reasons; temporary SQLite only."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from backend.app.models import CodexSession, Note
from backend.app.integrations.codex import knowledge_sources as sources
from backend.app.integrations.codex.evidence_coverage import conversation_coverage
from backend.tests.integrations.test_knowledge_sources import setup, insert_turn, URL


def test_registered_inventory_counts_sources_beyond_parent_caps_without_body_reads(tmp_path):
    engine = setup(tmp_path)
    try:
        with Session(engine) as db:
            db.get(CodexSession, 1).is_linked = False
            db.add_all(CodexSession(project_id=1, thread_id=f'local-{number}') for number in range(69))
            db.add_all(Note(project_id=1, kind='document', title=f'Ordinary {number}',
                            reference='manual.md', content='NOT_CONVERSATION') for number in range(64))
            db.flush()
            db.add_all(Note(project_id=1, kind='document', title=f'Shared {number}',
                            reference=URL, content='User:\nLOCAL_BODY\nChatGPT:\nUnderstood.')
                       for number in range(70))
            db.add(Note(project_id=2, kind='document', title='Foreign', reference=URL,
                        content='FOREIGN_BODY'))
            db.commit()
        statements = []
        def record(connection, cursor, statement, parameters, context, many):
            statements.append(statement)
        event.listen(engine, 'before_cursor_execute', record)
        result = sources.load_sources(engine, 1)
        event.remove(engine, 'before_cursor_execute', record)
        inventory = result['source_inventory']
        assert inventory['codex_sessions_total'] == 70
        assert inventory['linked_codex_sessions_total'] == 69
        assert inventory['codex_sessions_considered'] == 64
        assert inventory['linked_codex_sessions_considered'] == 63
        assert inventory['unlinked_codex_sessions_considered'] == 1
        assert inventory['linked_codex_sessions_omitted_parent_limit'] == 6
        assert inventory['document_notes_total'] == 134
        assert inventory['document_notes_considered'] == 64
        assert inventory['other_documents_considered'] == 64
        assert inventory['imported_chats_total'] == 70
        assert inventory['imported_chats_considered'] == 0
        assert inventory['imported_chats_omitted_parent_limit'] == 70
        assert inventory['omission_reasons']['empty_sessions'] == 63
        assert not result['sources']
        assert not any('SELECT notes.content' in statement for statement in statements)
        assert 'LOCAL_BODY' not in json.dumps(result) and 'FOREIGN_BODY' not in json.dumps(result)
        measured = conversation_coverage(result, {})
        assert measured['source_inventory'] == inventory
        measured['source_inventory']['omission_reasons']['empty_sessions'] = -1
        assert inventory['omission_reasons']['empty_sessions'] == 63
    finally:
        engine.dispose()


def test_imported_chat_sql_count_matches_strict_reference_matcher(tmp_path):
    engine = setup(tmp_path)
    references = [URL, URL.upper(), URL + '/', URL.replace('https', 'httpſ'),
                  URL + '?query', URL + '/extra', URL.replace('4000', 'g000'),
                  URL.replace('-4000-', '--000-'), URL + '\n', URL + '\x00suffix', None, 'manual.md', '']
    try:
        with Session(engine) as db:
            db.add_all(Note(project_id=1, title='Reference', kind='document', reference=ref,
                            content='User:\nChoose locally.\nChatGPT:\nUnderstood.') for ref in references)
            db.add(Note(project_id=1, title='Ordinary note', kind='note', reference=URL,
                        content='NOT_AN_IMPORTED_DOCUMENT'))
            db.commit()
        result = sources.load_sources(engine, 1)
        expected = sum(isinstance(ref, str) and bool(sources._SHARE.fullmatch(ref)) for ref in references)
        assert expected == 4
        assert result['source_inventory']['imported_chats_total'] == expected
        assert result['source_inventory']['imported_chats_considered'] == expected
        assert result['source_inventory']['imported_chats_omitted_parent_limit'] == 0
        assert result['source_inventory']['document_notes_total'] == len(references)
    finally:
        engine.dispose()


def test_inventory_metadata_does_not_change_evidence_or_manifest_identity(tmp_path, monkeypatch):
    engine = setup(tmp_path)
    try:
        with Session(engine) as db:
            insert_turn(db)
            db.commit()
        original = sources.load_sources(engine, 1)
        monkeypatch.setattr(sources, '_source_inventory', lambda *args: {'omission_reasons': {}, 'diagnostic': True})
        changed = sources.load_sources(engine, 1)
        assert changed['sources'] == original['sources']
        assert changed['manifest'] == original['manifest']
        assert changed['coverage'] == original['coverage']
        assert changed['source_inventory'] != original['source_inventory']
        old = deepcopy(original)
        old.pop('source_inventory')
        assert 'source_inventory' not in conversation_coverage(old, {})
    finally:
        engine.dispose()


def test_metadata_and_source_reads_share_one_sqlite_snapshot(tmp_path):
    engine = setup(tmp_path)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA journal_mode=WAL')
        with Session(engine) as db:
            db.add(Note(project_id=1, title='Original', kind='document', reference=URL,
                        content='User:\nOriginal.\nChatGPT:\nUnderstood.'))
            db.commit()
        added = False
        def concurrent_insert(connection, cursor, statement, parameters, context, many):
            nonlocal added
            if added or not statement.startswith('SELECT count(*) AS total') or 'codex_sessions' not in statement:
                return
            added = True
            with Session(engine) as writer:
                writer.add(CodexSession(project_id=1, thread_id='added-mid-read'))
                writer.add(Note(project_id=1, title='New', kind='document', reference=URL,
                                content='User:\nNew.\nChatGPT:\nUnderstood.'))
                writer.commit()
        event.listen(engine, 'before_cursor_execute', concurrent_insert)
        result = sources.load_sources(engine, 1)
        event.remove(engine, 'before_cursor_execute', concurrent_insert)
        assert added
        assert result['source_inventory']['codex_sessions_total'] == 1
        assert result['source_inventory']['imported_chats_total'] == 1
        assert not any('New.' in source['text'] for source in result['sources'])
        with Session(engine) as db:
            assert db.scalar(text('SELECT count(*) FROM codex_sessions WHERE project_id=1')) == 2
    finally:
        engine.dispose()


@pytest.mark.parametrize('case,reason', [
    ('active', 'active_turns'), ('oversized', 'oversized_turns'),
    ('text_budget', 'global_text_budget_turns'), ('invalid', 'invalid_turns'),
])
def test_skipped_turn_reason_uses_metadata_or_validation_boundary(tmp_path, monkeypatch, case, reason):
    engine = setup(tmp_path)
    try:
        with Session(engine) as db:
            insert_turn(db)
            if case == 'active':
                db.execute(text("UPDATE codex_history_turns SET status='inProgress' WHERE project_id=1"))
            elif case == 'invalid':
                db.execute(text("UPDATE codex_history_turns SET messages='not-json' WHERE project_id=1"))
            db.commit()
        if case == 'oversized':
            monkeypatch.setattr(sources, 'MAX_DOCUMENT_CHARS', 10)
        if case == 'text_budget':
            monkeypatch.setattr(sources, 'MAX_SOURCE_CHARS', 10)
        result = sources.load_sources(engine, 1)
        assert result['source_inventory']['omission_reasons'][reason] == 1
        assert not result['sources']
    finally:
        engine.dispose()


def test_document_and_message_budget_reasons_are_separate_units(tmp_path, monkeypatch):
    engine = setup(tmp_path)
    try:
        with Session(engine) as db:
            insert_turn(db)
            insert_turn(db, turn='second')
            db.add(Note(project_id=1, kind='document', title='Broken', reference=URL,
                        content='User:\nFirst\nUser:\nAmbiguous'))
            db.commit()
        monkeypatch.setattr(sources, 'MAX_MESSAGES', 1)
        result = sources.load_sources(engine, 1)
        reasons = result['source_inventory']['omission_reasons']
        assert reasons['global_message_budget_messages'] == 1
        assert reasons['global_message_budget_turns'] == 1
        assert reasons['invalid_documents'] == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize('case,reason', [
    ('oversized', 'oversized_documents'), ('text_budget', 'global_text_budget_documents'),
])
def test_imported_document_omission_reason_is_recorded_without_body_read(tmp_path, monkeypatch, case, reason):
    engine = setup(tmp_path)
    try:
        with Session(engine) as db:
            db.add(Note(project_id=1, kind='document', title='Shared', reference=URL,
                        content='User:\nDo not load this oversized body.\nChatGPT:\nUnderstood.'))
            db.commit()
        monkeypatch.setattr(sources, 'MAX_DOCUMENT_CHARS' if case == 'oversized' else 'MAX_SOURCE_CHARS', 10)
        statements = []
        def record(connection, cursor, statement, parameters, context, many):
            statements.append(statement)
        event.listen(engine, 'before_cursor_execute', record)
        result = sources.load_sources(engine, 1)
        event.remove(engine, 'before_cursor_execute', record)
        assert result['source_inventory']['omission_reasons'][reason] == 1
        assert result['source_inventory']['imported_chats_total'] == 1
        assert result['source_inventory']['imported_chats_considered'] == 1
        assert not any('SELECT notes.content' in statement for statement in statements)
        assert not result['sources']
    finally:
        engine.dispose()


def test_turn_row_cap_records_omitted_turns_beyond_bounded_query(tmp_path, monkeypatch):
    engine = setup(tmp_path)
    try:
        with Session(engine) as db:
            for number in range(5):
                insert_turn(db, turn=f'turn-{number}')
            db.commit()
        monkeypatch.setattr(sources, 'MAX_MESSAGES', 1)
        result = sources.load_sources(engine, 1)
        assert result['source_inventory']['omission_reasons']['turn_limit_turns'] == 3
        assert result['source_inventory']['omission_reasons']['global_message_budget_turns'] == 1
    finally:
        engine.dispose()
