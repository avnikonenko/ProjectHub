"""Cached exact-prefix metadata for indexed, linked project conversations."""
from collections import OrderedDict
import hashlib
import json
from threading import Lock

from sqlalchemy import text

from .shared_history import SharedPrefixIndex

_cache = OrderedDict()
_lock = Lock()


def _signature(turn):
    return hashlib.sha256(json.dumps(
        {'status': turn['status'], 'messages': turn['messages']},
        sort_keys=True, ensure_ascii=False).encode()).digest()


def indexed_sharing(db, project_id):
    # Metadata only: text is streamed once per changed index, not per page.
    markers = db.execute(text(
        'SELECT id,history_indexed_at,history_indexed_activity,thread_id,codex_account '
        'FROM codex_sessions WHERE project_id=:pid AND is_linked=1 '
        'AND history_indexed_at IS NOT NULL AND history_indexed_activity IS NOT NULL ORDER BY id'),
        {'pid': project_id}).all()
    if not markers:
        return SharedPrefixIndex(), {}
    key = (db.get_bind(), project_id, tuple(tuple(row) for row in markers))
    with _lock:
        saved = _cache.get(key)
        if saved is not None:
            _cache.move_to_end(key)
            return saved
    sharing, signatures = SharedPrefixIndex(), {}
    for marker in markers:
        session_id = marker[0]

        def turns():
            rows = db.execute(text('SELECT turn_id,status,messages FROM codex_history_turns '
                'WHERE project_id=:pid AND session_id=:sid ORDER BY id'),
                {'pid': project_id, 'sid': session_id})
            for row in rows:
                turn = {'id': row.turn_id, 'status': row.status, 'messages': json.loads(row.messages)}
                signatures[(session_id, row.turn_id)] = _signature(turn)
                yield turn

        sharing.add_session(session_id, turns())
    # Only duplicate signatures are needed to check a live history page against
    # the saved index; originals can always remain visible.
    signatures = {ref: signatures[ref] for ref in sharing.canonical}
    result = sharing, signatures
    with _lock:
        _cache[key] = result
        _cache.move_to_end(key)
        while len(_cache) > 8:
            _cache.popitem(last=False)
    return result


def annotate_page(db, project_id, session_id, page):
    sharing, signatures = indexed_sharing(db, project_id)
    turns = []
    for turn in page['turns']:
        ref = (session_id, turn['id'])
        target = sharing.canonical.get(ref)
        if target is not None and signatures.get(ref) == _signature(turn):
            title = db.execute(text('SELECT title FROM codex_sessions WHERE project_id=:pid AND id=:sid '
                                    'AND is_linked=1'), {'pid': project_id, 'sid': target[0]}).scalar()
            turn = {**turn, 'shared_history': {
                'session_id': target[0], 'turn_id': target[1], 'session_title': title or '',
            }}
        turns.append(turn)
    return {**page, 'turns': turns}


def deduplicate_sources(db, project_id, sources):
    """Keep one retrieved excerpt per confirmed shared turn, preserving aliases."""
    if not any('session_id' in source and 'turn_id' in source for source in sources):
        return sources
    sharing, _ = indexed_sharing(db, project_id)
    titles = dict(db.execute(text('SELECT id,title FROM codex_sessions WHERE project_id=:pid AND is_linked=1'),
                             {'pid': project_id}).all())
    seen, retained = set(), []
    for source in sources:
        if 'session_id' not in source or 'turn_id' not in source:
            retained.append(source)
            continue
        ref = source['session_id'], source['turn_id']
        canonical = sharing.canonical.get(ref, ref)
        if canonical in seen:
            continue
        seen.add(canonical)
        copies = [{'session_id': canonical[0], 'turn_id': canonical[1]},
                  *sharing.aliases.get(canonical, [])]
        aliases = [{**copy, 'session_title': titles.get(copy['session_id'], '')} for copy in copies
                   if (copy['session_id'], copy['turn_id']) != ref]
        retained.append({**source, **({'shared_sessions': aliases} if aliases else {})})
    return retained
