"""Focused excerpts stay bounded and address the original indexed text."""
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.integrations.codex import scheme_evidence as evidence
from backend.app.integrations.codex.scheme_passages import GAP, READ_CHARS
from backend.app.models import CodexSession, Note
from backend.tests.integrations.test_scheme_evidence import setup


def test_scope_reads_late_indexed_matches_and_preserves_unicode_offsets(tmp_path):
    engine = setup(tmp_path)
    prefix = 'Earlier résumé 🧭 context.\n\n' * 4000
    match = 'The unique checkpoint precedes indexing.'
    indexed_text = prefix + match + '\n\n' + 'Follow-up unrelated discussion.\n\n' * 1200
    try:
        with Session(engine) as db:
            db.add_all([CodexSession(id=100, project_id=1, title='Saved thread', thread_id='own'),
                        CodexSession(id=101, project_id=2, title='Foreign thread', thread_id='foreign')])
            db.flush()
            db.execute(text(
                'INSERT INTO codex_history_turns(project_id,session_id,session_title,turn_id,status,messages,content) '
                "VALUES(:pid,:session,'Thread','late','completed','[]',:content)"
            ), [{'pid': 1, 'session': 100, 'content': indexed_text},
                {'pid': 2, 'session': 101, 'content': match + ' Foreign private context.'}])
            db.commit()
        focused = evidence.collect_evidence(engine, 1, scope='unique checkpoint')
        sources = {source['id']: source for source in focused['sources']}
        assert 'session:101:turn:late' not in sources
        source = sources['session:100:turn:late']
        assert match in source['text']
        assert 0 < source['inspected_start'] < len(prefix)
        assert source['inspected_chars'] == READ_CHARS
        spans = source['excerpt_spans']
        assert any(span['start'] <= len(prefix) and span['end'] >= len(prefix) + len(match) for span in spans)
        assert source['text'] == GAP.join(indexed_text[span['start']:span['end']] for span in spans)
        regular = evidence.collect_evidence(engine, 1)
        assert all(match not in source['text'] for source in regular['sources'])
    finally:
        engine.dispose()


def test_title_search_and_clipped_paragraph_keep_original_read_rules(tmp_path):
    engine = setup(tmp_path)
    try:
        with Session(engine) as db:
            db.add_all([
                Note(id=100, project_id=1, title='Rare heading', content='Unrelated source text.\n\n' * 4000),
                Note(id=101, project_id=1, title='Long source', content='unrelated ' * READ_CHARS + 'Unique checkpoint'),
            ])
            db.commit()
        title = evidence.collect_evidence(engine, 1, scope='rare heading')
        note = next(source for source in title['sources'] if source['id'] == 'note:100')
        assert 'inspected_start' not in note
        assert note['inspected_chars'] == READ_CHARS
        focused = evidence.collect_evidence(engine, 1, scope='unique checkpoint')
        assert all(source['id'] != 'note:101' for source in focused['sources'])
        assert any('bounded windows' in detail for detail in focused['coverage']['limitations'])
    finally:
        engine.dispose()
