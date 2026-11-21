"""Bounded, anonymous conversation imports with explicit message provenance."""
import json
from pathlib import Path
import re
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener

from . import chatgpt_share

_MAX_BYTES = 16 * 1024 * 1024
_MAX_MESSAGES = 10000
_TIMEOUT = 15
_CLAUDE_PATH = re.compile(r'/share/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})/?', re.I | re.ASCII)
_UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', re.I | re.ASCII)
_ROLES = 'User|Assistant|Claude|ChatGPT'
_HEADER = re.compile(rf'^(?:#{{1,6}}[ \t]+({_ROLES}):?|\*\*({_ROLES}):?\*\*:?'
                     rf'|({_ROLES}):)[ \t]*$', re.I)
_OTHER_ROLE = re.compile(r'^(?:#{1,6}[ \t]+(?:System|Developer|Human|Tool):?'
                         r'|\*\*(?:System|Developer|Human|Tool):?\*\*:?'
                         r'|(?:System|Developer|Human|Tool):)[ \t]*$', re.I)


class ConversationImportError(ValueError):
    def __init__(self, message, *, code='invalid_file', status=None):
        if status is not None:
            message = f'{message} (HTTP {status})'
        super().__init__(message)
        self.code = code
        self.status = status


def _error(message, code='invalid_file', status=None):
    raise ConversationImportError(message, code=code, status=status)


def _unique_object(pairs):
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError('Duplicate JSON key')
        fields[key] = value
    return fields


def _text(value):
    if not isinstance(value, str):
        _error('Conversation text must be a string.')
    if '\x00' in value:
        _error('Conversation text contains unsupported NUL characters.')
    try:
        size = len(value.encode('utf-8'))
    except UnicodeError:
        _error('Conversation text contains invalid Unicode.')
    if size > _MAX_BYTES:
        _error('The conversation is too large to import.', 'too_large')
    return value


def _canonical_source(value):
    if not isinstance(value, str):
        _error('Enter a public ChatGPT or Claude share URL.', 'invalid_url')
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        _error('Enter a public ChatGPT or Claude share URL.', 'invalid_url')
    if parsed.netloc.lower() == 'chatgpt.com':
        try:
            return 'chatgpt', chatgpt_share.canonical_url(value)
        except ValueError:
            pass
    match = _CLAUDE_PATH.fullmatch(parsed.path)
    if parsed.scheme == 'https' and parsed.netloc.lower() == 'claude.ai' and match:
        return 'claude', f'https://claude.ai/share/{match[1].lower()}'
    _error('Enter a public https://chatgpt.com/share/ or https://claude.ai/share/ URL.', 'invalid_url')


def _provided_source(value):
    """Validate provenance only; this URL is never used as a fetch target."""
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        _error('The original conversation URL must be a bounded HTTPS URL.', 'invalid_url')
    value = value.strip()
    if re.search(r'[\s\x00-\x1f\x7f\\]', value):
        _error('The original conversation URL contains unsafe characters.', 'invalid_url')
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None):
            raise ValueError
        parsed.port  # Validate a supplied port without using it for a request.
        host = parsed.hostname.encode('idna').decode('ascii')
        if not re.fullmatch(r'[A-Za-z0-9.:-]+', host):
            raise ValueError
    except (ValueError, UnicodeError):
        _error('The original conversation URL must be a safe HTTPS URL without credentials.', 'invalid_url')
    try:
        return _canonical_source(value)[1]
    except ConversationImportError:
        return urlunsplit(('https', parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def _single_conversation(value):
    if isinstance(value, list):
        if len(value) != 1:
            _error('Extract one conversation from the account export, then import that conversation file.')
        value = value[0]
    if not isinstance(value, dict):
        _error('Import one conversation object, or an export list containing exactly one conversation.')
    if 'metadata' in value and not isinstance(value['metadata'], dict):
        _error('Conversation metadata must be an object.')
    return value


def _render(title, messages, provider, *, completeness='unknown', reference=None, source_status='none',
            provider_conversation_id=None):
    if not messages:
        _error('No readable user or assistant messages were found.', 'no_transcript')
    if len(messages) > _MAX_MESSAGES:
        _error('The conversation has too many messages.', 'too_large')
    parts, records = [], []
    length = total_bytes = 0
    for message in messages:
        role, body = message['role'], _text(message['text'])
        if not body.strip():
            _error('Conversation messages must not be empty.')
        prefix = ('\n\n' if records else '') + ('User' if role == 'user' else 'Assistant') + ':\n'
        total_bytes += len(prefix.encode()) + len(body.encode('utf-8'))
        if total_bytes > _MAX_BYTES:
            _error('The conversation is too large to import.', 'too_large')
        length += len(prefix)
        record = {'role': role, 'start': length, 'end': length + len(body)}
        for key in ('id', 'timestamp'):
            if key in message:
                record[key] = message[key]
        parts.extend((prefix, body))
        length += len(body)
        records.append(record)
    if {message['role'] for message in records} != {'user', 'assistant'}:
        completeness = 'partial'
    return {'title': _text(title)[:500] or 'Imported conversation', 'content': ''.join(parts),
            'reference': reference, 'conversation': {'provider': provider, 'completeness': completeness,
                'messages': records, 'source_status': source_status,
                **({'provider_conversation_id': provider_conversation_id} if provider_conversation_id else {})}}


def _provider_conversation_id(value, provider):
    if provider != 'claude':
        return None
    metadata = value.get('metadata', {})
    candidates = [value.get('provider_conversation_id'), metadata.get('provider_conversation_id')]
    if 'chat_messages' in value:
        candidates.append(value.get('uuid'))
    identities = set()
    for candidate in candidates:
        if candidate is None:
            continue
        if not isinstance(candidate, str) or not _UUID.fullmatch(candidate):
            _error('The Claude conversation identifier must be a UUID.')
        identities.add(candidate.lower())
    if len(identities) > 1:
        _error('The file contains conflicting Claude conversation identifiers.')
    return next(iter(identities), None)


def retained_claude_conversation_id(payload):
    """Recover an identity from an already-imported JSON source copy."""
    try:
        value = _single_conversation(json.loads(payload.decode('utf-8-sig'), object_pairs_hook=_unique_object))
        return _provider_conversation_id(value, 'claude')
    except (ValueError, RecursionError):
        return None


def _json_messages(value, *, provider_hint=None):
    value = _single_conversation(value)
    metadata = value.get('metadata', {})
    claude = 'chat_messages' in value
    raw = value.get('chat_messages') if claude else value.get('messages')
    if not isinstance(raw, list) or not raw:
        _error('A conversation must include a nonempty messages list.', 'no_transcript')
    if len(raw) > _MAX_MESSAGES:
        _error('The conversation has too many messages.', 'too_large')
    provider = 'claude' if claude else value.get('provider', metadata.get('provider', provider_hint or 'other'))
    if provider not in ('chatgpt', 'claude', 'other'):
        _error('Unknown conversation provider.')
    messages, seen_ids = [], set()
    for entry in raw:
        if not isinstance(entry, dict):
            _error('Each conversation message must be an object.')
        if 'metadata' in entry and not isinstance(entry['metadata'], dict):
            _error('Message metadata must be an object.')
        for attachment_key in ('attachments', 'files', 'files_v2', 'sync_sources'):
            if entry.get(attachment_key):
                _error('Messages containing attachments are not supported. Export a text-only conversation instead.')
        speaker = entry.get('sender') if claude else entry.get('role')
        role = {'human': 'user', 'assistant': 'assistant'}.get(speaker) if claude and isinstance(speaker, str) else speaker
        if role not in ('user', 'assistant'):
            _error('Each message must explicitly identify a user or assistant speaker.')
        body = entry.get('text') if claude else entry.get('content')
        if claude and (body is None or isinstance(body, str) and not body.strip()):
            body = entry.get('content')
        blocks = entry.get('content')
        if claude and blocks is not None:
            if not isinstance(blocks, (str, list)):
                _error('Only explicit text message blocks are supported.')
            if isinstance(blocks, list) and any(
                    not isinstance(part, dict) or part.get('type') != 'text'
                    or not isinstance(part.get('text'), str) for part in blocks):
                _error('Non-text or malformed message blocks are not supported. Export a text-only conversation instead.')
        if isinstance(body, list):
            if not all(isinstance(part, dict) and part.get('type') == 'text'
                       and isinstance(part.get('text'), str) for part in body):
                _error('Only explicit text message blocks are supported.')
            body = '\n'.join(part['text'] for part in body)
        message = {'role': role, 'text': _text(body)}
        message_id = entry.get('id')
        if message_id is None or isinstance(message_id, str) and not message_id.strip():
            message_id = entry.get('uuid')
        for key, candidate in (('id', message_id),
                               ('timestamp', entry.get('timestamp', entry.get('created_at')))):
            if candidate is not None:
                if not isinstance(candidate, str) or len(candidate) > 500:
                    _error('Message identifiers and timestamps must be bounded strings.')
                candidate = _text(candidate)
                if key == 'id':
                    if not candidate.strip():
                        continue
                    if candidate.strip() in seen_ids:
                        _error('Message identifiers must be unique within a conversation.')
                    seen_ids.add(candidate.strip())
                message[key] = candidate
        messages.append(message)
    completeness = value.get('completeness', metadata.get('completeness', 'unknown'))
    if completeness not in ('complete', 'partial', 'unknown'):
        _error('Unknown conversation completeness value.')
    return value.get('title', value.get('name', 'Imported conversation')), messages, provider, completeness


def _markdown_messages(text):
    messages, current = [], None
    position = 0
    fence = None
    labels = set()
    title = 'Imported conversation'
    for line in text.splitlines(keepends=True):
        bare = line.rstrip('\r\n')
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', bare)
        if marker:
            if current is None:
                _error('Code must appear within an explicitly labelled conversation message.')
            run = marker[1]
            if fence is None:
                fence = (run[0], len(run))
            elif run[0] == fence[0] and len(run) >= fence[1] and not bare[marker.end():].strip():
                fence = None
        elif fence is None:
            header = _HEADER.fullmatch(bare)
            if header:
                if current:
                    current['text'] = text[current.pop('body_start'):position]
                    messages.append(current)
                label = next(value for value in header.groups() if value is not None).lower()
                labels.add(label)
                current = {'role': 'user' if label == 'user' else 'assistant',
                           'body_start': position + len(line)}
            elif _OTHER_ROLE.fullmatch(bare):
                _error('Unsupported or ambiguous speaker header in the conversation.')
            elif re.match(rf'^(?:{_ROLES}):[ \t]*\S', bare, re.I):
                _error('Put speaker headers on their own line to avoid ambiguous message boundaries.')
            elif current is None and bare.startswith('# ') and title == 'Imported conversation':
                title = bare[2:].strip()
            elif current is None and bare.strip():
                _error('Markdown must identify each speaker with an explicit User or Assistant header.')
        position += len(line)
    if fence is not None:
        _error('The Markdown conversation contains an unclosed code fence.')
    if current:
        current['text'] = text[current.pop('body_start'):]
        messages.append(current)
    provider = 'claude' if 'claude' in labels and 'chatgpt' not in labels else (
        'chatgpt' if 'chatgpt' in labels and 'claude' not in labels else 'other')
    return title, messages, provider


def parse_file(filename, content, source_url=None):
    """Parse explicit JSON/Markdown conversations without guessing speaker roles."""
    if isinstance(content, bytes):
        if len(content) > _MAX_BYTES:
            _error('The conversation is too large to import.', 'too_large')
        try:
            content = content.decode('utf-8-sig')
        except UnicodeError:
            _error('Import a UTF-8 conversation file.')
    content = _text(content)
    suffix = Path(filename).suffix.lower() if isinstance(filename, str) else ''
    reference = _provided_source(source_url) if source_url is not None else None
    provider_conversation_id = None
    if suffix == '.json':
        try:
            value = json.loads(content, object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            _error('The conversation JSON is malformed.')
        value = _single_conversation(value)
        original_url = value.get('source_url', value.get('metadata', {}).get('source_url'))
        if source_url is None and original_url is not None:
            reference = _provided_source(original_url)
        title, messages, provider, completeness = _json_messages(value)
        provider_conversation_id = _provider_conversation_id(value, provider)
    elif suffix in ('.md', '.markdown', '.txt'):
        title, messages, provider = _markdown_messages(content)
        completeness = 'unknown'
    else:
        _error('Import a JSON or Markdown conversation file.', 'unsupported_format')
    return _render(title, messages, provider, completeness=completeness, reference=reference,
                   source_status='provided' if reference else 'none',
                   provider_conversation_id=provider_conversation_id)


def read_share(url):
    """Fetch only fixed public endpoints, without cookies, authorization or redirects."""
    provider, reference = _canonical_source(url)
    if provider == 'chatgpt':
        try:
            title, _, raw_messages = chatgpt_share.read_transcript(reference, return_messages=True)
        except chatgpt_share.ShareReadError as exc:
            status = exc.__cause__.code if isinstance(exc.__cause__, HTTPError) else None
            _error('Could not read the public ChatGPT conversation. Import its JSON or Markdown instead.',
                   'access_denied' if status in (401, 403) else 'share_unavailable', status)
        messages = []
        for raw in raw_messages:
            label, _, body = raw.partition(':\n')
            messages.append({'role': 'user' if label == 'User' else 'assistant', 'text': body})
        return _render(title, messages, provider, reference=reference, source_status='verified')
    snapshot = reference.rsplit('/', 1)[1]
    request = Request(f'https://claude.ai/api/chat_snapshots/{snapshot}', headers={
        'User-Agent': chatgpt_share._BROWSER_AGENT, 'Accept': 'application/json'})
    deadline = time.monotonic() + _TIMEOUT
    try:
        with build_opener(chatgpt_share._NoRedirect()).open(request, timeout=_TIMEOUT) as response:
            body = bytearray()
            while chunk := response.read1(65536):
                body.extend(chunk)
                if len(body) > _MAX_BYTES:
                    _error('The shared conversation is too large to import.', 'too_large')
                if time.monotonic() > deadline:
                    _error('The shared conversation request timed out.', 'share_unavailable')
    except HTTPError as exc:
        exc.close()
        _error('Claude did not allow anonymous access. Import the conversation JSON or Markdown instead.',
               'access_denied' if exc.code in (401, 403) else 'share_unavailable', exc.code)
    except (URLError, OSError, HTTPException):
        _error('Could not connect to the public Claude conversation.', 'share_unavailable')
    try:
        value = json.loads(body, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError):
        _error('Claude returned a page without readable conversation data. Import JSON or Markdown instead.',
               'no_transcript')
    if not isinstance(value, dict) or 'chat_messages' not in value:
        _error('Claude returned no readable conversation messages. Import JSON or Markdown instead.', 'no_transcript')
    title, messages, _, completeness = _json_messages(value, provider_hint='claude')
    if completeness == 'complete':
        completeness = 'unknown'
    return _render(title, messages, provider, completeness=completeness, reference=reference,
                   source_status='verified')
