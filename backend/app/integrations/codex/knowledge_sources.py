"""Bounded primary conversation sources and context-preserving extraction windows.

No model calls or source mutations. IDs describe local records, not invented remote
message IDs. Fragment hashes are independent of parent revisions for incremental reuse.
"""
import hashlib
import json
import re
from collections import deque

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ...models import CodexSession, Note, Project
from .protocol import CodexError

MAX_SOURCE_CHARS = 2_000_000
MAX_DOCUMENT_CHARS = 500_000
MAX_MESSAGES = 2000
_BALANCED_MAX_MESSAGES = 4000
_BALANCED_TEXT_HEADROOM = 250_000
_BALANCED_INTAKE = 'interleaved-v1'
FRAGMENT_CHARS = 6000
MAX_PARENTS = 64
_MARKER = re.compile(r'(?m)^(User|ChatGPT):[ \t]*(?:\r?\n)?')
_SHARE = re.compile(r'https://chatgpt\.com/share/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}/?', re.I)
_IMPORTED_TOOL_HEADER = re.compile(r'\[(external_agent_tool_call|external_agent_tool_result)(?::[^\]\r\n]*)?\]')
CONVERSATION_NOTE_SQL = "json_type(CASE WHEN json_valid(conversation) THEN conversation END) IS 'object'"

# SQLite metadata-only equivalent of _SHARE.fullmatch. A prefix LIKE alone would
# count ordinary references as imported chats and overstate the denominator.
_SHARE_SQL = """typeof(reference)='text'
    AND instr(reference,char(0))=0
    AND replace(lower(substr(reference,1,26)),'ſ','s')='https://chatgpt.com/share/'
    AND (length(reference)=62 OR (length(reference)=63 AND substr(reference,63,1)='/'))
    AND substr(reference,35,1)='-' AND substr(reference,40,1)='-'
    AND substr(reference,45,1)='-' AND substr(reference,50,1)='-'
    AND length(replace(substr(reference,27,36),'-',''))=32
    AND lower(replace(substr(reference,27,36),'-','')) NOT GLOB '*[^0-9a-f]*'"""
_IMPORTED_SQL = f'({CONVERSATION_NOTE_SQL} OR ({_SHARE_SQL}))'


def _source_inventory(db, project_id, sessions, notes):
    session_counts = db.execute(text('SELECT count(*) AS total, '
        'coalesce(sum(CASE WHEN is_linked THEN 1 ELSE 0 END),0) AS linked '
        'FROM codex_sessions WHERE project_id=:pid'), {'pid': project_id}).mappings().one()
    document_counts = db.execute(text(f'SELECT count(*) AS total, '
        f'coalesce(sum(CASE WHEN {_IMPORTED_SQL} THEN 1 ELSE 0 END),0) AS shared '
        "FROM notes WHERE project_id=:pid AND kind='document'"), {'pid': project_id}).mappings().one()
    considered_sessions, considered_documents = sessions[:MAX_PARENTS], notes[:MAX_PARENTS]
    linked = sum(bool(session.is_linked) for session in considered_sessions)
    imported = sum(note['canonical'] or (isinstance(note['reference'], str) and bool(_SHARE.fullmatch(note['reference'])))
                   for note in considered_documents)
    return {
        'scope': 'registered_project_sources',
        'codex_sessions_total': session_counts['total'],
        'linked_codex_sessions_total': session_counts['linked'],
        'codex_sessions_considered': len(considered_sessions),
        'linked_codex_sessions_considered': linked,
        'unlinked_codex_sessions_considered': len(considered_sessions) - linked,
        'codex_sessions_omitted_parent_limit': session_counts['total'] - len(considered_sessions),
        'linked_codex_sessions_omitted_parent_limit': session_counts['linked'] - linked,
        'document_notes_total': document_counts['total'],
        'document_notes_considered': len(considered_documents),
        'imported_chats_total': document_counts['shared'],
        'imported_chats_considered': imported,
        'imported_chats_omitted_parent_limit': document_counts['shared'] - imported,
        'other_documents_considered': len(considered_documents) - imported,
        'omission_reasons': {
            'parent_limit_linked_sessions': session_counts['linked'] - linked,
            'parent_limit_imported_chats': document_counts['shared'] - imported,
            'unlinked_sessions': len(considered_sessions) - linked,
        },
        'notes': ['Totals count registered local sources, not remote conversations or complete transcript content.',
                  'Session selection includes unlinked records; document selection includes ordinary document notes. These can occupy parent-limit slots before eligible conversations.',
                  'Omission reasons count different units (source groups, turns, documents or messages); do not add them into one total.'],
    }


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _messages(value):
    """Conservative adapter for legacy role-labelled transcripts, with exact offsets."""
    fenced, ignored = False, []
    offset = 0
    for line in value.splitlines(True):
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
        if fenced:
            ignored.append((offset, offset + len(line)))
        offset += len(line)
    if fenced:
        raise ValueError('Unclosed code fence makes transcript roles ambiguous')
    markers = [m for m in _MARKER.finditer(value)
               if not any(start <= m.start() < end for start, end in ignored)]
    if not markers or value[:markers[0].start()].strip() or markers[0][1] != 'User':
        raise ValueError('Transcript has no reliable initial user boundary')
    result = []
    previous = None
    for ordinal, marker in enumerate(markers, 1):
        role = 'user' if marker[1] == 'User' else 'assistant'
        if role == previous:
            raise ValueError('Repeated transcript roles make boundaries ambiguous')
        previous = role
        start = marker.end()
        end = markers[ordinal].start() if ordinal < len(markers) else len(value)
        body = value[start:end]
        start += len(body) - len(body.lstrip())
        end -= len(body) - len(body.rstrip())
        if end <= start:
            raise ValueError('Transcript contains an empty message')
        result.append((ordinal, role, value[start:end], start))
    return result


def _fragments(body):
    start, part = 0, 1
    while start < len(body):
        end = min(start + FRAGMENT_CHARS, len(body))
        if end < len(body):
            newline = body.rfind('\n', start + FRAGMENT_CHARS // 2, end)
            if newline >= 0:
                end = newline + 1
        yield part, start, end
        start, part = end, part + 1


def _canonical_message_ranges(size, metadata):
    """Validate the importer offsets without loading original message bodies."""
    if (not isinstance(metadata, dict) or metadata.get('completeness') not in ('complete', 'partial', 'unknown')
            or not isinstance(metadata.get('messages'), list) or not metadata['messages']):
        raise ValueError('Invalid imported conversation metadata')
    messages, previous_end, ids = [], 0, set()
    for ordinal, message in enumerate(metadata['messages'], 1):
        if not isinstance(message, dict):
            raise ValueError('Invalid imported message metadata')
        start, end, role = message.get('start'), message.get('end'), message.get('role')
        if (type(start) is not int or type(end) is not int or not previous_end <= start < end <= size
                or role not in ('user', 'assistant')):
            raise ValueError('Invalid imported message boundary')
        message_id = message.get('id')
        if message_id is not None:
            if not isinstance(message_id, str) or not message_id.strip() or len(message_id) > 512 or message_id in ids:
                raise ValueError('Invalid imported message identity')
            ids.add(message_id)
        timestamp = message.get('timestamp')
        messages.append((ordinal, role, start, end - start, message_id,
                         timestamp if isinstance(timestamp, str) else None))
        previous_end = end
    return messages


def _document_digest(db, note_id):
    """Hash the stored original without retaining it in an interleaved reader."""
    digest = hashlib.sha256()
    connection = db.connection().connection.driver_connection
    with connection.blobopen('notes', 'content', note_id, readonly=True) as blob:
        while chunk := blob.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def _imported_tool_block(body):
    value = body.strip()
    header = _IMPORTED_TOOL_HEADER.match(value)
    if header:
        start, closing = header.end(), f'[/{header[1]}]'
    elif value.startswith('<local-command-stdout>'):
        start, closing = len('<local-command-stdout>'), '</local-command-stdout>'
    else:
        return False
    return value.endswith(closing) and value.find(closing, start) == len(value) - len(closing)


def prior_context_ids(snapshot):
    return {ref['source_id'] for item in (snapshot or {}).get('items', [])
            for ref in item.get('context_dependencies', [])}


def needs_large_history_intake(engine, project_id):
    """Choose a broad intake when the indexed originals exceed the legacy budget."""
    with Session(engine) as db:
        turn_chars = db.scalar(text('SELECT coalesce(sum(length(h.messages)),0) '
            'FROM codex_history_turns h JOIN codex_sessions s '
            'ON s.id=h.session_id AND s.project_id=h.project_id '
            'WHERE h.project_id=:pid AND s.is_linked AND h.status<>\'inProgress\''), {'pid': project_id})
        document_chars = db.scalar(text(f'SELECT coalesce(sum(length(content)),0) '
            f"FROM notes WHERE project_id=:pid AND kind='document' AND {_IMPORTED_SQL}"), {'pid': project_id})
    return turn_chars + document_chars > MAX_SOURCE_CHARS


def load_sources(engine, project_id, previous_manifest=None, *, balanced=None, context_ids=()):
    """Return a bounded source snapshot; cached original text remains primary evidence.

    Coverage lists contain parent keys (processed/unprocessed/unavailable/excluded).
    Manifest availability refers to local evidence, not remote conversation deletion.
    """
    sources, entries = [], []
    if balanced is None:
        balanced = (previous_manifest or {}).get('intake_policy') == _BALANCED_INTAKE
    message_limit = _BALANCED_MAX_MESSAGES if balanced else MAX_MESSAGES
    text_limit = MAX_SOURCE_CHARS + (_BALANCED_TEXT_HEADROOM if balanced else 0)
    context_ids = set(context_ids)
    filtered_context = {}
    coverage = {key: [] for key in ('processed', 'unprocessed', 'unavailable', 'excluded', 'limitations')}
    chars = 0
    message_count = 0
    orders = {}
    inventory = {}

    def omitted(reason, count=1):
        reasons = inventory.setdefault('omission_reasons', {})
        reasons[reason] = reasons.get(reason, 0) + count

    def message_budget(size):
        if message_count >= message_limit:
            return 'global_message_budget_messages'
        if chars + size > text_limit:
            return 'global_text_budget_messages'
        return None

    def append(body, role, ordinal, parent, identity, label, offset=0, *, message_id=None, **metadata):
        nonlocal chars, message_count
        reason = message_budget(len(body))
        if reason:
            omitted(reason)
            return False
        message_count += 1
        chars += len(body)
        message_revision = _hash(body if message_id is None else [message_id, body])
        for part, start, end in _fragments(body):
            fragment = body[start:end]
            sources.append({
                'id': f'{identity}:p:{part}', 'label': label, 'conversation_id': parent,
                'source_key': parent, 'role': role, 'text': fragment,
                'revision': _hash(fragment if message_id is None else [message_id, fragment]),
                'message_revision': message_revision,
                'order': orders.get(parent, 0),
                'message_order': ordinal, 'timestamp': None,
                'offset_start': offset + start, 'offset_end': offset + end,
                'partial_message': len(body) > FRAGMENT_CHARS,
                **({'message_id': message_id} if message_id is not None else {}), **metadata,
            })
            orders[parent] = orders.get(parent, 0) + 1
        return True

    with Session(engine) as db:
        # Keep metadata counts, bounded content and historical availability checks
        # on one SQLite read snapshot without changing evidence fingerprints.
        db.execute(text('BEGIN'))
        project = db.get(Project, project_id)
        if project is None:
            raise CodexError('Project not found.')
        project_data = {key: getattr(project, key) for key in ('id', 'name', 'description', 'goals', 'repository_path')}
        sessions = list(db.scalars(select(CodexSession).where(
            CodexSession.project_id == project_id).order_by(CodexSession.id).limit(MAX_PARENTS + 1)))
        notes = list(db.execute(text(f'SELECT id,title,reference,length(content) AS size,{CONVERSATION_NOTE_SQL} AS canonical '
            "FROM notes WHERE project_id=:pid AND kind='document' ORDER BY id LIMIT :lim"),
            {'pid': project_id, 'lim': MAX_PARENTS + 1}).mappings())
        inventory = _source_inventory(db, project_id, sessions, notes)
        if len(sessions) > MAX_PARENTS or len(notes) > MAX_PARENTS:
            coverage['limitations'].append(
                f'Only the first {MAX_PARENTS} session records and {MAX_PARENTS} document notes were considered; '
                'unlinked sessions and ordinary document notes can occupy slots before eligible conversations.')
        def session_turns(session):
            parent = f'codex:{session.id}:{session.thread_id}'
            entry = {'source_key': parent, 'session_id': session.id, 'thread_id': session.thread_id,
                     'availability': 'available', 'revision': None}
            entries.append(entry)
            if not session.is_linked:
                entry['availability'] = 'excluded'
                entry['revision'] = _hash(['unlinked', session.thread_id])
                coverage['excluded'].append(parent)
                return
            total_turns = db.scalar(text('SELECT count(*) FROM codex_history_turns '
                'WHERE project_id=:pid AND session_id=:sid'), {'pid': project_id, 'sid': session.id})
            rows = db.execute(text('SELECT turn_id,status,length(messages) AS size, '
                "(instr(messages,'[external_agent_tool_') > 0 OR instr(messages,'<local-command-stdout>') > 0) AS has_tool_marker "
                'FROM codex_history_turns WHERE project_id=:pid AND session_id=:sid ORDER BY id LIMIT :lim'),
                {'pid': project_id, 'sid': session.id, 'lim': message_limit + 1})
            hashes, had_rows, considered_turns, admitted = [], False, 0, False
            filtered = False
            for row in rows.mappings():
                if had_rows:
                    yield
                had_rows = True
                considered_turns += 1
                reason = ('active_turns' if row['status'] == 'inProgress' else
                          'oversized_turns' if row['size'] > MAX_DOCUMENT_CHARS else
                          'global_message_budget_turns' if message_count >= message_limit else
                          'global_text_budget_turns' if not (balanced and row['has_tool_marker'])
                          and chars + row['size'] > text_limit else None)
                if reason:
                    omitted(reason)
                    coverage['unprocessed'].append(parent)
                    hashes.append([row['turn_id'], row['status'], row['size']])
                    continue
                try:
                    raw = db.scalar(text('SELECT messages FROM codex_history_turns WHERE project_id=:pid '
                        'AND session_id=:sid AND turn_id=:tid'),
                        {'pid': project_id, 'sid': session.id, 'tid': row['turn_id']})
                    messages = json.loads(raw)
                    if not isinstance(messages, list):
                        raise ValueError('Invalid messages')
                    for ordinal, message in enumerate(messages, 1):
                        if (not isinstance(message, dict) or message.get('role') not in ('user', 'assistant')
                                or not isinstance(message.get('text'), str)):
                            raise ValueError('Invalid message')
                        body, role = message['text'], message['role']
                        revision = _hash(body)
                        hashes.append([row['turn_id'], ordinal, role, revision])
                        identity = f'codex:{session.id}:turn:{_hash([session.thread_id, row["turn_id"]])[:24]}:m:{ordinal}'
                        if balanced and role == 'assistant' and _imported_tool_block(body):
                            filtered = True
                            omitted('imported_tool_blocks')
                            for part, start, end in _fragments(body):
                                identifier = f'{identity}:p:{part}'
                                order = orders.get(parent, 0)
                                if identifier in context_ids:
                                    filtered_context[identifier] = {
                                        'role': role, 'conversation_id': parent, 'order': order,
                                        'message_order': ordinal, 'message_revision': revision,
                                        'revision': _hash(body[start:end]),
                                    }
                                orders[parent] = order + 1
                            continue
                        if append(body, role, ordinal, parent, identity,
                                      f'{session.title} · turn {row["turn_id"]}',
                                      session_id=session.id, turn_id=row['turn_id']):
                            admitted |= bool(body)
                        else:
                            coverage['unprocessed'].append(parent)
                except (ValueError, TypeError, RecursionError):
                    omitted('invalid_turns')
                    coverage['unavailable'].append(parent)
            if total_turns > considered_turns:
                omitted('turn_limit_turns', total_turns - considered_turns)
                coverage['unprocessed'].append(parent)
            entry['revision'] = _hash(hashes)
            if not had_rows:
                omitted('empty_sessions')
                entry['availability'] = 'unavailable'
                coverage['unavailable'].append(parent)
            elif parent in coverage['unavailable']:
                entry['availability'] = 'unavailable'
            elif admitted:
                coverage['processed'].append(parent)
            elif filtered and parent not in coverage['unprocessed']:
                entry.update(availability='excluded', reason='imported_tool_logs_only')
                coverage['excluded'].append(parent)
            if parent in coverage['unprocessed']:
                entry['partial'] = True
            if (not session.history_indexed_at or not session.history_indexed_activity
                    or session.history_indexed_activity != session.codex_activity_at or session.last_error):
                coverage['limitations'].append(f'{parent}: index freshness or remote availability is not established; stored evidence was used.')
        def document_messages(note):
            shared = isinstance(note['reference'], str) and bool(_SHARE.fullmatch(note['reference']))
            if not note['canonical'] and not shared:
                return
            parent = f'chatgpt:note:{note["id"]}'
            entry = {'source_key': parent, 'note_id': note['id'], 'availability': 'available', 'revision': None}
            if shared:
                entry['url'] = note['reference']
            entries.append(entry)
            if not note['canonical'] and (note['size'] > MAX_DOCUMENT_CHARS or chars + note['size'] > text_limit):
                omitted('oversized_documents' if note['size'] > MAX_DOCUMENT_CHARS else 'global_text_budget_documents')
                entry['availability'] = 'excluded'
                entry['revision'] = _hash(['excluded_by_limit', note['size']])
                coverage['excluded'].append(parent)
                return
            sliced = note['canonical'] and note['size'] > MAX_DOCUMENT_CHARS
            params = {'pid': project_id, 'nid': note['id']}
            row = db.execute(text('SELECT CASE WHEN :sliced THEN NULL ELSE content END,conversation '
                                  'FROM notes WHERE project_id=:pid AND id=:nid'),
                             {**params, 'sliced': sliced}).first()
            value, raw_metadata = row if row is not None else (None, None)
            if row is None or (not sliced and not isinstance(value, str)):
                omitted('invalid_documents')
                entry.update(availability='unavailable', reason='changed_during_read')
                coverage['unavailable'].append(parent)
                return
            try:
                if note['canonical']:
                    metadata = json.loads(raw_metadata)
                    entry['revision'] = (_hash(['sliced', _document_digest(db, note['id']), metadata])
                                         if sliced else _hash([value, metadata]))
                    ranges = _canonical_message_ranges(note['size'] if sliced else len(value), metadata)
                    if not sliced and any(not value[start:start + size].strip()
                                          for _, _, start, size, _, _ in ranges):
                        raise ValueError('Empty imported message')
                    completeness = metadata['completeness']
                    entry['transcript_completeness'] = completeness
                    if completeness == 'partial':
                        coverage['limitations'].append(f'{parent}: the imported transcript is partial. Only stored messages are available.')
                    elif completeness == 'unknown':
                        coverage['limitations'].append(f'{parent}: completeness of the imported transcript is not established.')
                else:
                    entry['revision'] = _hash(value)
                    ranges = [(order, role, start, len(body), None, None)
                              for order, role, body, start in _messages(value)]
            except (ValueError, TypeError, RecursionError):
                omitted('invalid_documents')
                entry['availability'] = 'unavailable'
                coverage['unavailable'].append(parent)
                return
            for ordinal, role, offset, size, message_id, timestamp in ranges:
                if ordinal > 1:
                    yield
                if sliced:
                    reason = message_budget(size)
                    if reason:
                        omitted(reason)
                        coverage['unprocessed'].append(parent)
                        continue
                    body = db.scalar(text('SELECT substr(content,:start,:size) FROM notes '
                                          'WHERE project_id=:pid AND id=:nid'),
                                     {**params, 'start': offset + 1, 'size': size})
                    if not isinstance(body, str) or len(body) != size or not body.strip():
                        omitted('invalid_messages')
                        entry['availability'] = 'unavailable'
                        coverage['unavailable'].append(parent)
                        continue
                else:
                    body = value[offset:offset + size]
                identity = f'{parent}:mid:{_hash(message_id)[:24]}' if message_id is not None else f'{parent}:m:{ordinal}'
                if not append(body, role, ordinal, parent, identity,
                              f'{note["title"]} · message {ordinal}', offset,
                              message_id=message_id, note_id=note['id'], reference=note['reference'],
                              **({'timestamp': timestamp} if timestamp is not None else {})):
                    coverage['unprocessed'].append(parent)
            if orders.get(parent):
                coverage['processed'].append(parent)
            if parent in coverage['unprocessed']:
                entry['partial'] = True
        # Spend one turn (or imported message) per conversation per round.
        # Preserve chronological, conversation-contiguous output for extraction.
        readers = deque([*(session_turns(session) for session in sessions[:MAX_PARENTS]),
                         *(document_messages(note) for note in notes[:MAX_PARENTS])])
        if balanced:
            pending = object()
            while readers:
                reader = readers.popleft()
                if next(reader, pending) is not pending:
                    readers.append(reader)
            parent_order = {entry['source_key']: index for index, entry in enumerate(entries)}
            sources.sort(key=lambda source: parent_order[source['source_key']])
        else:
            for reader in readers:
                for _ in reader:
                    pass
        current_keys = {entry['source_key'] for entry in entries}
        previous_entries = (previous_manifest or {}).get('entries', [])
        for old in previous_entries:
            if old['source_key'] in current_keys:
                continue
            # A record omitted by limits/type changes is excluded, not deleted.
            exists = False
            if old.get('note_id') is not None:
                exists = db.scalar(select(Note.id).where(Note.project_id == project_id,
                                                        Note.id == old['note_id'])) is not None
            elif old.get('session_id') is not None:
                exists = db.scalar(select(CodexSession.id).where(CodexSession.project_id == project_id,
                                                                CodexSession.id == old['session_id'])) is not None
            availability = 'excluded' if exists else 'unavailable'
            reason = 'not_selected_as_primary_source' if exists else 'missing_local_record'
            # An absent local record cannot establish deletion of the remote chat.
            entries.append({**old, 'availability': availability, 'reason': reason})
            coverage[availability].append(old['source_key'])
    if any(s['partial_message'] for s in sources):
        coverage['limitations'].append('Messages above 6,000 characters were split into exact, newline-aware fragments; distant proposal context may be incomplete.')
    if inventory['omission_reasons'].get('imported_tool_blocks'):
        coverage['limitations'].append('Imported tool logs remain searchable in History and are excluded from AI conversation evidence.')
    if coverage['unprocessed'] or coverage['excluded']:
        coverage['limitations'].append('Source limits or exclusions prevented complete extraction coverage.')
    if coverage['unavailable']:
        coverage['limitations'].append('Some local source records were unavailable or had ambiguous role boundaries; this does not establish remote deletion.')
    for key in coverage:
        coverage[key] = list(dict.fromkeys(coverage[key]))
    manifest = {'entries': entries, 'project_revision': _hash(project_data),
                'fingerprint': _hash({'entries': entries, 'project': project_data})}
    if balanced:
        manifest['intake_policy'] = _BALANCED_INTAKE
    return {'project': project_data, 'sources': sources, 'manifest': manifest, 'coverage': coverage,
            'source_inventory': inventory, **({'filtered_context': filtered_context} if filtered_context else {})}


def has_heading_continuation(source, following):
    """Detect a heading continued in the same message, independently of size limits."""
    if not source.get('partial_message') or source.get('role') != 'assistant':
        return False
    tail = source['text'].rstrip().split('\n')[-1].strip()
    if not re.fullmatch(r'(?:#{1,6}\s+|\d+[.)]\s+)\S.{0,198}[^.!?;:]', tail):
        return False
    if (following.get('role') != 'assistant'
            or not source.get('message_revision')
            or any(source.get(key) != following.get(key) for key in (
                'conversation_id', 'message_revision', 'message_order'))
            or type(source.get('offset_end')) is not int
            or following.get('offset_start') != source['offset_end']):
        return False
    return True


def heading_continuation(source, following):
    """Return a literal, bounded continuation of an incomplete assistant heading.

    Revision identifies the original fragment; the explicit view marker describes
    the smaller text sent for context. Never follow a heading into another message.
    """
    if not has_heading_continuation(source, following):
        return None
    text = following['text']
    # Include leading whitespace so offsets and exact-source quotation remain true.
    leading = len(text) - len(text.lstrip())
    boundary = re.search(r'\n\s*\n', text[leading:])
    end = leading + boundary.start() if boundary else len(text)
    if not text[:end].strip() or end > 1000:
        return None
    return {**following, 'text': text[:end],
            'offset_end': following['offset_start'] + end,
            'context_view': 'heading-continuation-v1'}


def build_windows(bundle, max_chars=14000):
    """Pack stable forward primary groups with previous same-conversation context.

    Context-only fragments must not produce duplicate extracted items. Serialized
    windows, including evidence metadata, stay below the supplied character cap.
    """
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1000:
        raise ValueError('Knowledge window limit must be an integer of at least 1,000 characters.')
    windows = []
    group = []
    previous = None
    context = []

    def payload(items, context_items):
        value = {'sources': [*context_items, *items], 'primary_ids': [s['id'] for s in items],
                 'context_ids': [s['id'] for s in context_items]}
        return value

    def size(items, context_items):
        return len(json.dumps({**payload(items, context_items), 'fingerprint': '0' * 64}, ensure_ascii=False))

    def flush():
        if not group:
            return
        value = payload(group, context)
        value['fingerprint'] = _hash(value)
        windows.append(value)

    for source in bundle['sources']:
        if previous is not None:
            different_conversation = source['conversation_id'] != previous['conversation_id']
            skipped_message = (source.get('note_id') is not None
                               and source['message_order'] > previous['message_order'] + 1)
            if different_conversation or skipped_message:
                # An omitted message can contain a different proposal or the
                # real antecedent of a short acceptance after the gap.
                flush()
                group, context = [], []
                previous = None
        if not group:
            context = [previous] if previous else []
            if size([source], context) > max_chars:
                context = []
            if size([source], context) > max_chars:
                raise CodexError('A knowledge source fragment exceeds the extraction window limit.')
        if group and size([*group, source], context) > max_chars:
            flush()
            context = [previous] if previous else []
            group = []
            if size([source], context) > max_chars:
                context = []
            if size([source], context) > max_chars:
                raise CodexError('A knowledge source fragment exceeds the extraction window limit.')
        group.append(source)
        previous = source
    flush()
    positions = {source['id']: index for index, source in enumerate(bundle['sources'])}
    for value in windows:
        last = next(source for source in reversed(value['sources'])
                    if source['id'] == value['primary_ids'][-1])
        index = positions[last['id']] + 1
        if index >= len(bundle['sources']):
            continue
        view = heading_continuation(last, bundle['sources'][index])
        if view is None or view['id'] in {source['id'] for source in value['sources']}:
            continue
        amended = {**value, 'sources': [*value['sources'], view],
                   'context_ids': [*value['context_ids'], view['id']]}
        if len(json.dumps(amended, ensure_ascii=False)) <= max_chars:
            amended['fingerprint'] = _hash({key: item for key, item in amended.items() if key != 'fingerprint'})
            value.update(amended)
    return windows
