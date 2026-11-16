"""Cached exact-prefix metadata for indexed, linked project conversations."""
from collections import OrderedDict
import hashlib
import json
import logging
from threading import Lock

from sqlalchemy import text

from .shared_history import SharedPrefixIndex

log = logging.getLogger(__name__)
_cache = OrderedDict()
_lock = Lock()
MAX_CACHE_SESSIONS = 1000
MAX_INDEX_TURNS = 20_000
MAX_CACHED_TURNS = 40_000
MAX_ID_CHARS = 4 * 1024 * 1024


def _retire_project(engine, project_id):
    """Caller holds _lock; obsolete revisions never keep the same project alive."""
    for key in list(_cache):
        if key[0] is engine and key[1] == project_id:
            del _cache[key]


def _turn_count(value):
    return sum(len(turns) for turns in value[0]._session_turns.values())


def _signature(turn):
    return hashlib.sha256(json.dumps(
        {'status': turn['status'], 'messages': turn['messages']},
        sort_keys=True, ensure_ascii=True).encode()).digest()


def _markers(db, project_id):
    return db.execute(text(
        'SELECT id,history_indexed_at,history_indexed_activity,thread_id,codex_account,codex_activity_at '
        'FROM codex_sessions WHERE project_id=:pid AND is_linked=1 '
        'AND history_indexed_at IS NOT NULL AND history_indexed_activity IS NOT NULL '
        'AND (codex_activity_at IS NULL OR codex_activity_at=history_indexed_activity) ORDER BY id LIMIT :limit'),
        {'pid': project_id, 'limit': MAX_CACHE_SESSIONS + 1}).all()


class _IndexBudgetExceeded(Exception):
    """Optional sharing metadata must never exceed its actual read budget."""


def indexed_sharing(db, project_id):
    # Metadata only: text is streamed once per changed index, not per page.
    markers = _markers(db, project_id)
    engine = db.get_bind()
    if not markers or len(markers) > MAX_CACHE_SESSIONS:
        with _lock:
            _retire_project(engine, project_id)
        return SharedPrefixIndex(), {}
    key = (engine, project_id, tuple(tuple(row) for row in markers))
    with _lock:
        saved = _cache.get(key)
        if saved is not None:
            _cache.move_to_end(key)
            return saved
        _retire_project(engine, project_id)
    # Deduplication is optional navigation metadata. Above this budget preserve
    # all original turns/excerpts instead of allocating a giant prefix index.
    count, chars = db.execute(text(
        'SELECT count(*),coalesce(sum(length(h.turn_id)),0) FROM codex_history_turns h '
        'JOIN codex_sessions s ON s.id=h.session_id AND s.project_id=h.project_id '
        'WHERE h.project_id=:pid AND s.is_linked=1 AND s.history_indexed_at IS NOT NULL '
        'AND s.history_indexed_activity IS NOT NULL '
        'AND (s.codex_activity_at IS NULL OR s.codex_activity_at=s.history_indexed_activity)'),
        {'pid': project_id}).one()
    marker_chars = sum(len(str(value)) for row in markers for value in row if value is not None)
    if count > MAX_INDEX_TURNS or chars + marker_chars > MAX_ID_CHARS:
        return SharedPrefixIndex(), {}
    sharing, signatures = SharedPrefixIndex(), {}
    actual_count, actual_chars = 0, marker_chars

    def turns(session_id):
        nonlocal actual_count, actual_chars
        # A refresh can commit after the preflight query. Check what we really
        # read, before decoding messages or retaining their hashes/references.
        # Read only the remaining budget plus one sentinel row.
        rows = db.execute(text('SELECT turn_id,status,messages FROM codex_history_turns '
            'WHERE project_id=:pid AND session_id=:sid ORDER BY id LIMIT :limit'),
            {'pid': project_id, 'sid': session_id, 'limit': MAX_INDEX_TURNS - actual_count + 1})
        try:
            for row in rows:
                actual_count += 1
                actual_chars += len(str(row.turn_id))
                if actual_count > MAX_INDEX_TURNS or actual_chars > MAX_ID_CHARS:
                    raise _IndexBudgetExceeded
                try:
                    messages = json.loads(row.messages)
                    if (not isinstance(messages, list) or any(
                            not isinstance(message, dict) or message.get('role') not in ('user', 'assistant')
                            or not isinstance(message.get('text'), str) for message in messages)):
                        raise ValueError('Unsupported indexed message structure')
                    signature = _signature({'status': row.status, 'messages': messages})
                except (ValueError, TypeError, RecursionError):
                    # Sharing is optional. An unreadable saved row must not
                    # prevent browsing valid live history or join both sides
                    # of an unknown prefix gap.
                    log.warning('Ignoring shared-prefix detection after an unreadable indexed turn '
                                'in project %s session %s.', project_id, session_id)
                    messages, signature = None, None
                turn = {'id': row.turn_id, 'status': row.status, 'messages': messages}
                signatures[(session_id, row.turn_id)] = signature
                yield turn
        finally:
            rows.close()

    try:
        for marker in markers:
            sharing.add_session(marker[0], turns(marker[0]))
    except _IndexBudgetExceeded:
        return SharedPrefixIndex(), {}
    # Only duplicate signatures are needed to check a live history page against
    # the saved index; originals can always remain visible.
    signatures = {ref: signatures[ref] for ref in sharing.canonical}
    result = sharing, signatures
    with _lock:
        # Do not cache a mixture of sessions from different index revisions.
        # Avoid a long SQLite read transaction: sync writers stay unblocked,
        # while a changed index simply loses this optional optimization.
        if tuple(tuple(row) for row in _markers(db, project_id)) != key[2]:
            return SharedPrefixIndex(), {}
        _retire_project(engine, project_id)
        _cache[key] = result
        _cache.move_to_end(key)
        while len(_cache) > 8 or sum(_turn_count(value) for value in _cache.values()) > MAX_CACHED_TURNS:
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
    seen, retained = {}, []
    for source in sources:
        if 'session_id' not in source or 'turn_id' not in source:
            retained.append(source)
            continue
        ref = source['session_id'], source['turn_id']
        canonical = sharing.canonical.get(ref, ref)
        # Two excerpts from one shared turn may cover different parts of a
        # long message (for example a recent tail and a matching FTS snippet).
        # Collapse only identical excerpts; never discard complementary facts.
        excerpt = source.get('text')
        excerpts = seen.setdefault(canonical, set())
        if isinstance(excerpt, str):
            if excerpt in excerpts:
                continue
            excerpts.add(excerpt)
        copies = [{'session_id': canonical[0], 'turn_id': canonical[1]},
                  *sharing.aliases.get(canonical, [])]
        aliases = [{**copy, 'session_title': titles.get(copy['session_id'], '')} for copy in copies
                   if (copy['session_id'], copy['turn_id']) != ref]
        retained.append({**source, **({'shared_sessions': aliases} if aliases else {})})
    return retained
