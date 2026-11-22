"""Focused excerpts stay bounded and address the original indexed text."""
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.integrations.codex import scheme_evidence as evidence
from backend.app.integrations.codex.scheme_passages import EXCERPT_CHARS, GAP, READ_CHARS
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


def test_focused_note_uses_body_match_when_a_single_paragraph_is_too_long(tmp_path):
    engine = setup(tmp_path)
    content = 'Earlier résumé 🧭 context ' * READ_CHARS + 'Unique checkpoint'
    try:
        with Session(engine) as db:
            db.add_all([
                Note(id=100, project_id=1, title='Rare heading', content='Unrelated source text.\n\n' * 4000),
                Note(id=101, project_id=1, title='Long source', content=content),
                Note(id=102, project_id=1, title='Rare heading', content='Different body ' * READ_CHARS),
            ])
            db.commit()
        title = evidence.collect_evidence(engine, 1, scope='rare heading')
        note = next(source for source in title['sources'] if source['id'] == 'note:100')
        assert 'inspected_start' not in note
        assert note['inspected_chars'] == READ_CHARS
        assert all(source['id'] != 'note:102' for source in title['sources'])
        focused = evidence.collect_evidence(engine, 1, scope='unique checkpoint')
        note = next(source for source in focused['sources'] if source['id'] == 'note:101')
        assert note['text'].endswith('Unique checkpoint')
        assert len(note['text']) <= EXCERPT_CHARS
        span = note['excerpt_spans'][0]
        assert set(span) == {'start', 'end'}
        assert span['end'] == len(content)
        assert note['text'] == content[span['start']:span['end']]
        assert any('bounded windows' in detail for detail in focused['coverage']['limitations'])
        assert any('text around a focused match' in detail for detail in focused['coverage']['limitations'])
        assert all(source['id'] != 'note:101' for source in evidence.collect_evidence(engine, 1)['sources'])
    finally:
        engine.dispose()


def test_focused_turn_falls_back_even_when_the_entire_long_paragraph_was_read(tmp_path):
    engine = setup(tmp_path)
    content = ('User: ' + 'Padding ' * EXCERPT_CHARS + 'workflow sentinel ' + 'final context ' * 100
               + '\n\nThe workflow is discussed generally.')
    assert EXCERPT_CHARS < len(content) < READ_CHARS
    try:
        with Session(engine) as db:
            db.add(CodexSession(id=100, project_id=1, title='Saved thread', thread_id='own'))
            db.flush()
            db.execute(text(
                'INSERT INTO codex_history_turns(project_id,session_id,session_title,turn_id,status,messages,content) '
                "VALUES(1,100,'Thread','long','completed','[]',:content)"
            ), {'content': content})
            db.commit()
        focused = evidence.collect_evidence(engine, 1, scope='workflow sentinel')
        source = next(result for result in focused['sources'] if result['id'] == 'session:100:turn:long')
        assert 'workflow sentinel' in source['text']
        [span] = source['excerpt_spans']
        assert set(span) == {'start', 'end'}
        assert source['text'] == content[span['start']:span['end']]
        assert 0 < span['start'] < span['end'] < len(content)
        assert len(source['text']) <= EXCERPT_CHARS
        assert 'match_start' not in source and 'match_length' not in source
    finally:
        engine.dispose()


def test_focused_excerpt_keeps_complete_passage_over_unrelated_prefix_match(tmp_path):
    engine = setup(tmp_path)
    content = 'Padding ' * EXCERPT_CHARS + 'auditorium seating.\n\nThis is complete background evidence.'
    try:
        with Session(engine) as db:
            db.add(Note(id=100, project_id=1, title='Long source', content=content))
            db.commit()
        focused = evidence.collect_evidence(engine, 1, scope='audit')
        source = next(result for result in focused['sources'] if result['id'] == 'note:100')
        assert source['text'] == 'This is complete background evidence.'
        assert all('text around a focused match' not in detail for detail in focused['coverage']['limitations'])
    finally:
        engine.dispose()
