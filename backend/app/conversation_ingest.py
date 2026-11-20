"""Project-scoped preview identities and immutable conversation source copies."""
import hashlib
import json
from collections import Counter

from sqlalchemy import func, literal_column, or_, select

from .integrations import conversation_import
from .models import ConversationImportSource, Note
from .schemas import NoteCreate


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def prepare(payload):
    if payload.url is not None:
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
    if payload.filename is not None:
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


def _match_retained_claude_uuid(db, matches, provider_id, stored_id, stored_provider):
    # Older file imports retained the raw Claude export but did not index its UUID.
    source = ConversationImportSource
    latest_source = (select(source.id).where(source.project_id == Note.project_id, source.note_id == Note.id)
                     .order_by(source.id.desc()).limit(1).correlate(Note).scalar_subquery())
    candidates = matches.limit(None).add_columns(source.payload).join(source, source.id == latest_source).where(
        stored_id.is_(None), stored_provider == 'claude', source.filename.ilike('%.json'))
    for row in db.execute(candidates.execution_options(yield_per=1)):
        if conversation_import.retained_claude_conversation_id(row.payload) == provider_id:
            return row
    return None


def _find_existing(db, project_id, values):
    metadata = values['conversation']
    matches = select(Note.id, Note.title, Note.updated_at, Note.reference, Note.conversation).where(
        Note.project_id == project_id, Note.kind == 'document').order_by(Note.id).limit(1)
    reference = values['reference']
    if reference:
        found = db.execute(matches.where(Note.reference == reference)).first()
        if found is not None:
            return found
        matches = matches.where(Note.reference == '')
    provider_id = metadata.get('provider_conversation_id')
    stored_id = func.json_extract(Note.conversation, literal_column("'$.provider_conversation_id'"))
    stored_provider = func.json_extract(Note.conversation, literal_column("'$.provider'"))
    if metadata['provider'] != 'other':
        matches = matches.where(or_(stored_provider.is_(None), stored_provider.in_(('other', metadata['provider']))))
    if provider_id:
        found = db.execute(matches.where(stored_id == provider_id, stored_provider == metadata['provider'])).first()
        if found is not None:
            return found
        found = _match_retained_claude_uuid(db, matches, provider_id, stored_id, stored_provider)
        if found is not None:
            return found
        matches = matches.where(or_(stored_id.is_(None), stored_id == provider_id))
    found = db.execute(matches.where(Note.conversation.is_not(None),
        func.json_extract(Note.conversation, literal_column("'$.fingerprint'")) == metadata['fingerprint'],
    )).first()
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
