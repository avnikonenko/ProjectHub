"""Explicit, atomic indexing of full saved user/assistant conversation text."""
import json
import re
from secrets import token_hex
import tempfile
import threading

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from ...models import Project, CodexSession, utcnow
from .knowledge_sources import CONVERSATION_NOTE_SQL
from .protocol import CodexError

PAGE_SIZE = 20
_index_lock = threading.Lock()
_HEX = '[0-9a-f]'
_SHARE_REFERENCE = 'https://chatgpt.com/share/' + '-'.join(_HEX * n for n in (8, 4, 4, 4, 12))
_CODEX_MATCHES = (
    'SELECT h.session_id,s.title AS session_title,h.turn_id, '
    "snippet(codex_history_fts,0,:highlight_start,:highlight_end,'…',40) AS snippet, codex_history_fts.rank AS score "
    'FROM codex_history_fts JOIN codex_history_turns h ON h.id=codex_history_fts.rowid '
    'JOIN codex_sessions s ON s.id=h.session_id AND s.project_id=h.project_id '
    'WHERE codex_history_fts MATCH :query AND h.project_id=:pid AND s.is_linked=1'
)
_ACTIVE_COUNT = (
    'SELECT count(*) FROM codex_history_turns h JOIN codex_sessions s '
    'ON s.id=h.session_id AND s.project_id=h.project_id WHERE h.project_id=:pid AND s.is_linked=1'
)
_CHATGPT_MATCHES = (
    "SELECT NULL,NULL,NULL,snippet(search_index,-1,:highlight_start,:highlight_end,'…',40),search_index.rank, "
    "'chatgpt',n.id,n.title,n.created_at "
    'FROM search_index JOIN notes n ON n.id=search_index.entity_id AND n.project_id=search_index.project_id '
    "WHERE search_index MATCH :query AND search_index.entity_type='notes' AND n.project_id=:pid "
    f"AND n.kind='document' AND ({CONVERSATION_NOTE_SQL} OR lower(n.reference) GLOB :reference)"
)


class HistoryIndexBusy(CodexError):
    pass


def index_history(engine, project_id, manager, *, session_ids=None, cancelled=None):
    if not _index_lock.acquire(blocking=False):
        raise HistoryIndexBusy('A history index refresh is already running. Retry after it finishes.')
    try:
        def check_cancelled():
            if cancelled is not None and cancelled():
                raise CodexError('History indexing was cancelled. The previous index is preserved.')

        check_cancelled()
        with Session(engine) as db:
            if db.get(Project, project_id) is None:
                raise CodexError('Project not found.')
            all_sessions = list(db.scalars(select(CodexSession).where(CodexSession.project_id == project_id,
                                CodexSession.is_linked.is_(True))
                                .order_by(CodexSession.id)))
            identities = {(session.id, session.thread_id, session.codex_account) for session in all_sessions}
            if session_ids is not None:
                selected = set(session_ids)
                if not selected <= {session.id for session in all_sessions}:
                    raise CodexError('History indexing requested a missing or foreign project session.')
                if not selected:
                    state = db.execute(text('SELECT indexed_at,indexed_turns FROM codex_history_index '
                                            'WHERE project_id=:pid'), {'pid': project_id}).mappings().first()
                    return dict(state) if state else {'indexed_at': None, 'indexed_turns': 0}
                sessions = [session for session in all_sessions if session.id in selected]
            else:
                sessions = all_sessions
        incomplete_sessions = set()
        # Spool text to disk before the write transaction. A slow or failed read
        # neither locks project writes nor replaces a previously usable index.
        with tempfile.TemporaryFile(mode='w+t', encoding='utf-8') as staged:
            if sessions:
                client = manager._new_read_client()
                try:
                    for session in sessions:
                        check_cancelled()
                        cursor, cursors, seen = None, set(), set()
                        while True:
                            check_cancelled()
                            page = manager.history(session, cursor, client=client, archive=True)
                            for turn in page['turns']:
                                if turn['status'] == 'inProgress':
                                    incomplete_sessions.add(session.id)
                                    continue
                                if turn['id'] in seen:
                                    continue
                                seen.add(turn['id'])
                                messages = [m for m in turn['messages'] if m['role'] in ('user', 'assistant')]
                                content = '\n\n'.join(m['text'] for m in messages)
                                if not content.strip():
                                    continue
                                staged.write(json.dumps({'project_id': project_id, 'session_id': session.id,
                                    'session_title': session.title, 'turn_id': turn['id'], 'status': turn['status'],
                                    'messages': json.dumps(messages, ensure_ascii=False), 'content': content},
                                    ensure_ascii=False) + '\n')
                            cursor = page['next_cursor']
                            if cursor is None:
                                break
                            if cursor in cursors:
                                raise CodexError('Codex history returned a repeated pagination cursor.')
                            cursors.add(cursor)
                finally:
                    client.close()
            indexed_at = utcnow()
            staged.seek(0)
            with engine.begin() as connection:
                connection.exec_driver_sql('BEGIN IMMEDIATE')
                # Recheck ownership immediately before replacing persisted data.
                check_cancelled()
                current = {tuple(row) for row in connection.execute(text(
                    'SELECT id,thread_id,codex_account FROM codex_sessions WHERE project_id=:pid AND is_linked=1'),
                    {'pid': project_id})}
                if identities != current:
                    raise CodexError('Session associations changed during history indexing. Retry the refresh.')
                if session_ids is None:
                    connection.execute(text('DELETE FROM codex_history_turns WHERE project_id=:pid AND session_id IN '
                                            '(SELECT id FROM codex_sessions WHERE project_id=:pid AND is_linked=1)'),
                                       {'pid': project_id})
                else:
                    connection.execute(text('DELETE FROM codex_history_turns WHERE project_id=:pid '
                                            'AND session_id IN :sessions').bindparams(bindparam('sessions', expanding=True)),
                                       {'pid': project_id, 'sessions': sorted(selected)})
                insert = text('INSERT INTO codex_history_turns '
                              '(project_id,session_id,session_title,turn_id,status,messages,content) '
                              'VALUES (:project_id,:session_id,:session_title,:turn_id,:status,:messages,:content)')
                for line in staged:
                    connection.execute(insert, json.loads(line))
                for session in sessions:
                    connection.execute(text('UPDATE codex_sessions SET history_indexed_at=:at, '
                        'history_indexed_activity=:activity WHERE id=:sid AND project_id=:pid AND is_linked=1'),
                        {'at': indexed_at, 'activity': None if session.id in incomplete_sessions else session.codex_activity_at,
                         'sid': session.id, 'pid': project_id})
                count = connection.execute(text(_ACTIVE_COUNT),
                                           {'pid': project_id}).scalar_one()
                connection.execute(text('INSERT INTO codex_history_index(project_id,indexed_at,indexed_turns) '
                    'VALUES (:pid,:at,:count) ON CONFLICT(project_id) DO UPDATE SET '
                    'indexed_at=excluded.indexed_at,indexed_turns=excluded.indexed_turns'),
                    {'pid': project_id, 'at': indexed_at, 'count': count})
                check_cancelled()
            return {'indexed_at': indexed_at, 'indexed_turns': count}
    finally:
        _index_lock.release()


def update_active_count(db, project_id):
    db.execute(text(f'UPDATE codex_history_index SET indexed_turns=({_ACTIVE_COUNT}) WHERE project_id=:pid'),
               {'pid': project_id})


def append_search_match(parts, highlighted):
    # Keep message and line boundaries out of phrase highlights.
    if len(parts) >= 2 and parts[-2]['match'] and re.fullmatch(r'[ \t]{1,8}', parts[-1]['text']):
        gap = parts.pop()['text']
        parts[-1]['text'] += gap + highlighted
    else:
        parts.append({'text': highlighted, 'match': True})


def _snippet_parts(snippet, start, end):
    parts = []
    plain, *matches = snippet.split(start)
    if plain:
        parts.append({'text': plain, 'match': False})
    for entry in matches:
        highlighted, _, plain = entry.partition(end)
        if highlighted:
            append_search_match(parts, highlighted)
        if plain:
            parts.append({'text': plain, 'match': False})
    return parts


def search_markers():
    # A fresh delimiter distinguishes SQLite highlights from literal brackets
    # or markup in saved content.
    marker = token_hex(16)
    return f'<{marker}>', f'</{marker}>'


def search_history(db, project_id, query, offset=0, *, include_shared=False):
    state = db.execute(text('SELECT indexed_at,indexed_turns FROM codex_history_index WHERE project_id=:pid'),
                       {'pid': project_id}).mappings().first()
    result = {'results': [], 'next_offset': None, 'indexed_at': state['indexed_at'] if state else None,
              'indexed_turns': state['indexed_turns'] if state else 0}
    terms = re.findall(r'\w+', query, flags=re.UNICODE)[:30]
    if not terms:
        return result
    match = ' AND '.join('"' + term + '"' for term in terms)
    start, end = search_markers()
    params = {'query': match, 'pid': project_id, 'limit': PAGE_SIZE + 1, 'offset': offset,
              'highlight_start': start, 'highlight_end': end}
    if include_shared:
        # Apply the page limit after merging both sources so offsets never skip a match.
        query_sql = (
            "SELECT c.*, 'codex' AS kind, NULL AS note_id, NULL AS title, NULL AS note_created_at "
            f'FROM ({_CODEX_MATCHES}) c UNION ALL {_CHATGPT_MATCHES} '
            'ORDER BY score,kind,session_id,turn_id,note_id'
        )
        params['reference'] = _SHARE_REFERENCE
    else:
        query_sql = _CODEX_MATCHES + ' ORDER BY score,session_id,turn_id'
    rows = db.execute(text(query_sql + ' LIMIT :limit OFFSET :offset'), params).mappings().all()
    for row in rows[:PAGE_SIZE]:
        if include_shared and row['kind'] == 'chatgpt':
            keys = ('kind', 'note_id', 'title', 'snippet', 'note_created_at')
        else:
            keys = ('session_id', 'session_title', 'turn_id', 'snippet')
            if include_shared:
                keys += ('kind',)
        item = {key: row[key] for key in keys}
        item['snippet_parts'] = _snippet_parts(row['snippet'], start, end)
        item['snippet'] = row['snippet'].replace(start, '[').replace(end, ']')
        result['results'].append(item)
    if len(rows) > PAGE_SIZE:
        result['next_offset'] = offset + PAGE_SIZE
    return result


def indexed_turn(db, project_id, session_id, turn_id):
    row = db.execute(text('SELECT h.turn_id,h.status,h.messages FROM codex_history_turns h '
        'JOIN codex_sessions s ON s.id=h.session_id AND s.project_id=h.project_id '
        'WHERE h.project_id=:pid AND h.session_id=:sid AND h.turn_id=:tid'),
        {'pid': project_id, 'sid': session_id, 'tid': turn_id}).mappings().first()
    return {'id': row['turn_id'], 'status': row['status'], 'messages': json.loads(row['messages'])} if row else None
