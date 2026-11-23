"""Project-scoped preview identities and immutable conversation source copies."""
import hashlib
import json
from collections import Counter

from fastapi import HTTPException
from sqlalchemy import func, literal_column, or_, select

from .integrations import chatgpt_share, conversation_import
from .models import ConversationImportSource, Note
from .schemas import NoteCreate


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def prepare(payload, engine=None):
    if payload.session_id is not None:
        from .integrations.claude_session import read_session
        normalized, original = read_session(engine, payload.session_id, payload.profile, payload.title)
    elif payload.url is not None:
        normalized = conversation_import.read_share(payload.url)
    else:
        normalized = conversation_import.parse_file(payload.filename, payload.content.encode('utf-8'), payload.source_url)
    values = NoteCreate(title=normalized['title'], content=normalized['content'],
                        reference=normalized['reference'] or '', kind='document').model_dump()
    metadata = normalized['conversation']
    messages = metadata['messages']
    occurrences = Counter()
    identifiers = {message['id'] for message in messages if message.get('id')}
    contents = []
    for message in messages:
        content = values['content'][message['start']:message['end']]
        identity = [message['role'], content]
        contents.append(identity)
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        message['content_hash'] = content_hash
        if not message.get('id'):
            key = f"import:{message['role']}:{content_hash[:32]}"
            while True:
                occurrences[key] += 1
                identifier = f'{key}:{occurrences[key]}'
                if identifier not in identifiers:
                    break
            message['id'] = identifier
            identifiers.add(message['id'])
    metadata['fingerprint'] = _digest(contents)
    values['conversation'] = metadata
    if payload.session_id is not None:
        filename = 'claude-code-session.json'
    elif payload.filename is not None:
        filename = payload.filename.replace('\\', '/').rsplit('/', 1)[-1]
        original = payload.content.encode('utf-8')
    else:
        # Retain a reproducible normalized source copy for URL imports. The
        # provider's HTML and incidental response data are unnecessary.
        filename = 'conversation.json'
        original = json.dumps({'title': values['title'], 'provider': metadata['provider'],
                               'source_url': values['reference'], 'completeness': metadata['completeness'],
                               'messages': [{**{key: message[key] for key in ('id', 'role', 'timestamp') if key in message},
                                             'content': content} for message, (_, content) in zip(messages, contents)]},
                              ensure_ascii=False, indent=2).encode('utf-8')
    source = {'filename': filename, 'reference': values['reference'], 'payload': original,
              'content_hash': hashlib.sha256(original).hexdigest()}
    return values, source


def _retained_claude_sources(matches):
    # Older file imports retained the raw Claude export but did not index its UUID.
    source = ConversationImportSource
    latest_source = (select(source.id).where(source.project_id == Note.project_id, source.note_id == Note.id)
                     .order_by(source.id.desc()).limit(1).correlate(Note).scalar_subquery())
    return matches.add_columns(source.payload).join(source, source.id == latest_source).where(
        source.filename.ilike('%.json'))


def _match_retained_claude_uuid(db, matches, provider_id, stored_id, stored_provider):
    candidates = _retained_claude_sources(matches.limit(None)).where(stored_id.is_(None), stored_provider == 'claude')
    for row in db.execute(candidates.execution_options(yield_per=1)):
        if conversation_import.retained_claude_conversation_id(row.payload) == provider_id:
            return row
    return None


def _linked_claude_id(metadata):
    session = metadata.get('linked_session')
    return session.get('id') if isinstance(session, dict) and session.get('provider') == 'claude' else None


def _check_claude_identity(found, provider_id, retained_id=None):
    previous = found.conversation or {}
    if previous.get('provider') not in (None, 'other', 'claude') or any(
        identity is not None and identity != provider_id
        for identity in (previous.get('provider_conversation_id'), _linked_claude_id(previous), retained_id)
    ):
        raise HTTPException(409, 'This source belongs to a different saved conversation. Check its source link and identifier.')


def _find_existing(db, project_id, values):
    metadata = values['conversation']
    matches = select(Note.id, Note.title, Note.updated_at, Note.reference, Note.conversation).where(
        Note.project_id == project_id, Note.kind == 'document').order_by(Note.id).limit(1)
    reference = values['reference']
    provider_id = metadata.get('provider_conversation_id')
    known_claude_id = provider_id and metadata['provider'] == 'claude'
    stored_id = func.json_extract(Note.conversation, literal_column("'$.provider_conversation_id'"))
    stored_provider = func.json_extract(Note.conversation, literal_column("'$.provider'"))
    identity_match = None
    if known_claude_id:
        linked_id = func.json_extract(Note.conversation, literal_column("'$.linked_session.id'"))
        matching_id = matches.where(stored_provider == 'claude', or_(stored_id == provider_id, linked_id == provider_id))
        # Prefer the linked row if a project already has duplicates from older imports.
        matching_id = matching_id.order_by(None).order_by((linked_id == provider_id).desc(), Note.id)
        identity_match = db.execute(matching_id).first()
        if identity_match is None:
            identity_match = _match_retained_claude_uuid(db, matches, provider_id, stored_id, stored_provider)
        if identity_match is not None:
            _check_claude_identity(identity_match, provider_id)
    if reference:
        saved_conversation = Note.conversation.is_not(None)
        try:
            legacy_share = chatgpt_share.canonical_url(reference) == reference
        except ValueError:
            legacy_share = False
        if legacy_share:
            # Preserve exact duplicates of a one-sided share. Replacing changed
            # text also requires the original import's two-speaker framing.
            saved_conversation = or_(saved_conversation, Note.content == _legacy_chatgpt_content(values),
                                     Note.content.op('GLOB')('User:\n?*\n\nChatGPT:\n?*'),
                                     Note.content.op('GLOB')('ChatGPT:\n?*\n\nUser:\n?*'))
        same_reference = matches.where(Note.reference == reference, saved_conversation)
        if known_claude_id:
            reference_matches = db.execute(same_reference.limit(2)).all()
            if len(reference_matches) > 1:
                raise HTTPException(409, 'This source link matches more than one saved conversation. Check the source link.')
            found = next(iter(reference_matches), None)
        else:
            found = db.execute(same_reference).first()
        if found is not None:
            if known_claude_id:
                if identity_match is not None and identity_match.id != found.id:
                    raise HTTPException(409, 'The source link and identifier match different saved conversations.')
                retained = None
                if (found.conversation or {}).get('provider') == 'claude' and not found.conversation.get(
                        'provider_conversation_id'):
                    retained = db.execute(_retained_claude_sources(same_reference.where(Note.id == found.id))).first()
                retained_id = conversation_import.retained_claude_conversation_id(retained.payload) if retained else None
                _check_claude_identity(found, provider_id, retained_id)
            return found
        matches = matches.where(Note.reference == '')
    if identity_match is not None:
        return identity_match
    if metadata['provider'] != 'other':
        matches = matches.where(or_(stored_provider.is_(None), stored_provider.in_(('other', metadata['provider']))))
    if provider_id:
        matches = matches.where(or_(stored_id.is_(None), stored_id == provider_id))
    found = db.execute(matches.where(Note.conversation.is_not(None),
        func.json_extract(Note.conversation, literal_column("'$.fingerprint'")) == metadata['fingerprint'],
    )).first()
    if found is not None and known_claude_id:
        _check_claude_identity(found, provider_id)
    if found is not None or reference or metadata['provider'] == 'claude':
        return found
    return db.execute(matches.where(Note.conversation.is_(None),
        Note.reference.like('https://chatgpt.com/share/%'), Note.content == _legacy_chatgpt_content(values))).first()


def _legacy_chatgpt_content(values):
    return '\n\n'.join(('User' if msg['role'] == 'user' else 'ChatGPT') + ':\n'
                       + values['content'][msg['start']:msg['end']] for msg in values['conversation']['messages'])


def preserve_source_identity(values, previous_reference, previous_metadata):
    """Keep known identity when a replacement file does not declare it."""
    metadata = values['conversation']
    if previous_reference and not values['reference']:
        values['reference'] = previous_reference
        metadata['source_status'] = 'provided'
    if previous_metadata and metadata['provider'] == 'other':
        metadata['provider'] = previous_metadata['provider']
    if previous_metadata and previous_metadata['provider'] == metadata['provider']:
        provider_id = previous_metadata.get('provider_conversation_id')
        if provider_id:
            metadata.setdefault('provider_conversation_id', provider_id)
        linked_id = _linked_claude_id(previous_metadata)
        if linked_id and metadata.get('provider_conversation_id', linked_id) == linked_id:
            metadata.setdefault('linked_session', previous_metadata['linked_session'])


def _is_duplicate(db, project_id, found, values):
    previous = found.conversation
    if previous is None:
        content = db.scalar(select(Note.content).where(Note.project_id == project_id, Note.id == found.id))
        if content == values['content']:
            return True
        return found.reference.startswith('https://chatgpt.com/share/') and content == _legacy_chatgpt_content(values)
    return (not values['reference'] or values['reference'] == found.reference) and all(
        value == previous.get(key) for key, value in values['conversation'].items()
        if key != 'source_status' or value != 'none')


def preview(db, project_id, values, source):
    metadata = values['conversation']
    found = _find_existing(db, project_id, values)
    if found is not None and metadata.get('linked_session') and found.reference.startswith('https://'):
        # Keep the original link on the conversation. The new source copy
        # separately records its local Claude Code session reference.
        values['reference'] = found.reference
        metadata['source_status'] = found.conversation.get('source_status', 'provided')
    duplicate = found is not None and _is_duplicate(db, project_id, found, values)
    existing = {'id': found.id, 'title': found.title, 'duplicate': duplicate} if found is not None else None
    first_and_last = sorted(set(range(min(2, len(metadata['messages'])))) |
                            set(range(max(0, len(metadata['messages']) - 2), len(metadata['messages']))))
    counts = Counter(message['role'] for message in metadata['messages'])
    sample = [{'role': message['role'], 'text': values['content'][message['start']:min(message['end'], message['start'] + 500)]}
              for i in first_and_last for message in (metadata['messages'][i],)]
    return {'title': values['title'], 'provider': metadata['provider'], 'completeness': metadata['completeness'],
            'source_status': metadata['source_status'], 'reference': values['reference'],
            'message_count': len(metadata['messages']), 'role_counts': {role: counts[role] for role in ('user', 'assistant')},
            'sample': sample, 'existing': existing,
            'fingerprint': _digest([project_id, values['title'], values['reference'], metadata,
                                    source['filename'], source['content_hash'],
                                    [found.id, found.title, found.updated_at] if found is not None else None])}
