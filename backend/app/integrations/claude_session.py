"""Read the user-visible text of a local Claude Code session."""
import errno
import json
import os
import re
import stat
from contextlib import ExitStack
from itertools import count

from .codex.accounts import claude_launch_home, claude_profile_entries
from .conversation_import import _MAX_BYTES, _MAX_MESSAGES, _UUID, _error, _render


_MAX_LOG_BYTES = 64 * 1024 * 1024
_MAX_ROWS = 250_000
_MAX_WORKSPACES = 4096
_MAX_PROFILES = 64
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
_CONTROL_COMMANDS = {'model', 'effort', 'config', 'permissions', 'status', 'help', 'doctor',
                     'clear', 'export', 'resume', 'exit', 'quit'}
_COMMAND = re.compile(r'<command-name>/([a-z-]+)</command-name>\s*<command-message>[^<]*</command-message>'
                      r'\s*<command-args>[^<]*</command-args>')


def _profiles(engine, profile):
    profiles = [('launch', claude_launch_home())]
    if profile != 'launch':
        profiles.extend((key, path) for key, _, path in claude_profile_entries(engine))
    if profile is not None:
        profiles = [(key, root) for key, root in profiles if key == profile]
        if not profiles:
            _error('The selected Claude Code profile is unavailable.', 'session_not_found')
    return profiles


def _open(path, flags, parent=None):
    try:
        return os.open(path, flags, dir_fd=parent)
    except OSError as exc:
        if exc.errno not in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
            raise
        return None


def _workspace_transcripts(root, filename, scanned):
    # A registered profile can be an alias. From its resolved root onwards,
    # only follow physical entries under projects/<workspace>/<session>.jsonl.
    with ExitStack() as directories:
        root_fd = _open(root, _DIRECTORY)
        if root_fd is None:
            return
        directories.callback(os.close, root_fd)
        projects = _open('projects', _DIRECTORY, root_fd)
        if projects is None:
            return
        directories.callback(os.close, projects)
        with os.scandir(projects) as workspaces:
            for workspace in workspaces:
                if next(scanned) >= _MAX_WORKSPACES:
                    _error('The Claude Code workspace search limit was reached. Select a profile or import a file.',
                           'too_large')
                if not workspace.is_dir(follow_symlinks=False):
                    continue
                workspace_fd = _open(workspace.name, _DIRECTORY, projects)
                if workspace_fd is None:
                    continue
                try:
                    transcript = _open(filename, _FILE, workspace_fd)
                finally:
                    os.close(workspace_fd)
                if transcript is not None:
                    yield transcript


def _read_text(entry):
    role = entry.get('type')
    if (role not in ('user', 'assistant') or entry.get('isMeta') or entry.get('isCompactSummary')
            or entry.get('isVisibleInTranscriptOnly') or entry.get('toolUseResult') is not None):
        return None
    message = entry.get('message')
    if not isinstance(message, dict) or message.get('role') != role:
        _error('The Claude Code transcript contains a malformed message.')
    content = message.get('content')
    if isinstance(content, list):
        if any(not isinstance(block, dict) for block in content):
            _error('The Claude Code transcript contains malformed message blocks.')
        blocks = [block.get('text') for block in content if block.get('type') == 'text']
        if not all(isinstance(block, str) for block in blocks):
            _error('The Claude Code transcript contains malformed text blocks.')
        content = '\n'.join(blocks)
    if not isinstance(content, str):
        _error('The Claude Code transcript contains malformed message text.')
    if not content.strip() or content.lstrip().startswith((
            '<local-command-stdout>', '<local-command-stderr>', '<local-command-caveat>')):
        return None
    command = _COMMAND.fullmatch(content.strip()) if role == 'user' else None
    if command and command[1] in _CONTROL_COMMANDS:
        return None
    record = {'role': role, 'text': content, 'id': entry['uuid']}
    timestamp = entry.get('timestamp')
    if isinstance(timestamp, str) and len(timestamp) <= 64 and timestamp.isascii() and timestamp.isprintable():
        record['timestamp'] = timestamp
    return record


def _active_messages(nodes, latest):
    records, visited = [], set()
    while latest in nodes:
        if latest in visited:
            _error('The Claude Code transcript contains a cyclic message history.')
        visited.add(latest)
        latest, message = nodes[latest]
        if message is not None:
            records.append(message)
    return list(reversed(records)), 'partial' if latest is not None else 'unknown'


def _read_transcript(handle, session_id, title):
    before = os.fstat(handle.fileno())
    if before.st_size > _MAX_LOG_BYTES:
        _error('The local Claude Code transcript exceeds the 64 MiB read limit.', 'too_large')
    nodes, latest = {}, None
    fallback_title, custom_title = None, None
    total = 0
    for index, line in enumerate(iter(lambda: handle.readline(_MAX_BYTES + 1), b'')):
        total += len(line)
        if len(line) > _MAX_BYTES or total > _MAX_LOG_BYTES:
            _error('The local Claude Code transcript exceeds the read limit.', 'too_large')
        if index >= _MAX_ROWS:
            _error('The local Claude Code transcript has too many records.', 'too_large')
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError):
            message = ('Claude Code is still writing the transcript. Try again after the turn finishes.'
                       if not line.endswith(b'\n') else 'The Claude Code transcript contains invalid JSON.')
            _error(message)
        if not isinstance(entry, dict):
            _error('The Claude Code transcript contains an invalid record.')
        if entry.get('sessionId') != session_id or entry.get('isSidechain'):
            continue
        entry_type = entry.get('type')
        if entry_type == 'custom-title' and isinstance(entry.get('customTitle'), str):
            custom_title = entry['customTitle']
        elif entry_type == 'ai-title' and isinstance(entry.get('aiTitle'), str):
            fallback_title = entry['aiTitle']
        identifier = entry.get('uuid')
        if not isinstance(identifier, str) or not _UUID.fullmatch(identifier):
            if entry_type in ('user', 'assistant'):
                _error('The Claude Code transcript contains an invalid message ID.')
            continue
        parent = entry.get('parentUuid', latest)
        if parent is not None and (not isinstance(parent, str) or not _UUID.fullmatch(parent)):
            _error('The Claude Code transcript contains an invalid parent message ID.')
        record = _read_text(entry)
        if identifier in nodes:
            if nodes[identifier] != (parent, record):
                _error('The Claude Code transcript contains conflicting message records.')
            continue
        nodes[identifier] = (parent, record)
        if entry_type in ('user', 'assistant'):
            latest = identifier
    after = os.fstat(handle.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        _error('Claude Code updated the transcript while it was being read. Try again.')
    records, completeness = _active_messages(nodes, latest)
    if len(records) > _MAX_MESSAGES:
        _error('The conversation has too many messages.', 'too_large')
    return (title or custom_title or fallback_title or f'Claude Code session {session_id[:8]}',
            records, completeness)


def read_session(engine, session_id, profile=None, title=None):
    if not isinstance(session_id, str) or not _UUID.fullmatch(session_id):
        _error('Enter a valid Claude Code session UUID.')
    session_id = session_id.lower()
    seen_roots, seen_files = set(), set()
    scanned = count()
    try:
        with ExitStack() as matches:
            found = None
            for key, root in _profiles(engine, profile):
                root = root.resolve()
                if root in seen_roots:
                    continue
                if len(seen_roots) >= _MAX_PROFILES:
                    _error('The Claude Code profile search limit was reached. Select a profile.', 'too_large')
                seen_roots.add(root)
                for fd in _workspace_transcripts(root, session_id + '.jsonl', scanned):
                    with os.fdopen(fd, 'rb') as candidate:
                        file = os.fstat(candidate.fileno())
                        identity = (file.st_dev, file.st_ino)
                        if not stat.S_ISREG(file.st_mode) or identity in seen_files:
                            continue
                        seen_files.add(identity)
                        if found is not None:
                            message = ('This session has multiple transcripts in the selected profile. Import the intended conversation as a file.'
                                       if profile else 'This session exists in more than one location. Select its Claude Code profile.')
                            _error(message, 'ambiguous_session')
                        found = (key, matches.enter_context(os.fdopen(os.dup(candidate.fileno()), 'rb')))
            if found is None:
                _error('No local Claude Code transcript was found for this ID.', 'session_not_found')
            key, handle = found
            name, messages, completeness = _read_transcript(handle, session_id, title)
    except OSError:
        _error('ProjectHub could not read the local Claude Code transcript.', 'session_unavailable')
    normalized = _render(name, messages, 'claude', completeness=completeness,
                         reference=f'claude-code:{session_id}', source_status='provided',
                         provider_conversation_id=session_id)
    metadata = normalized['conversation']
    metadata['linked_session'] = {'provider': 'claude', 'id': session_id, 'profile': key}
    # Save reproducible user/assistant text without tool results, request blobs,
    # account metadata, private reasoning or local paths from runtime metadata.
    copy = json.dumps({
        'title': normalized['title'], 'provider': 'claude', 'provider_conversation_id': session_id,
        'completeness': metadata['completeness'],
        'messages': [{**{k: row[k] for k in ('id', 'role', 'timestamp') if k in row}, 'content': row['text']}
                     for row in messages],
    }, ensure_ascii=False, separators=(',', ':')).encode()
    if len(copy) > _MAX_BYTES:
        _error('The saved copy of the Claude Code conversation is too large.', 'too_large')
    return normalized, copy
