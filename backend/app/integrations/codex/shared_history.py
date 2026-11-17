"""Conservatively identify exact inherited prefixes without retaining message text.

Create a separate index per project. Sessions must be supplied oldest turn first;
adding a session does not modify it or its original history records.
"""
import hashlib
import json
from collections.abc import Iterable

TurnRef = tuple[int, str]
_TERMINAL_STATUSES = frozenset(('completed', 'interrupted', 'failed'))
_INITIAL_HASH = hashlib.sha256(b'ProjectHub exact history prefix v1').digest()
_MIN_TURNS = 2
_MIN_CHARS = 200


def _turn_content(turn):
    """Return a canonical byte representation, or a conservative prefix barrier."""
    if not isinstance(turn, dict):
        return None
    turn_id, status, messages = turn.get('id'), turn.get('status'), turn.get('messages')
    if (not isinstance(turn_id, str) or not turn_id
            or not isinstance(status, str) or status not in _TERMINAL_STATUSES
            or not isinstance(messages, list) or not messages):
        return None
    content = []
    chars = 0
    substantive = False
    for message in messages:
        if (not isinstance(message, dict) or message.get('role') not in ('user', 'assistant')
                or not isinstance(message.get('text'), str)):
            return None
        text = message['text']
        substantive = substantive or bool(text.strip())
        chars += len(text.strip())
        content.append([message['role'], text])
    if not substantive:
        return None
    # ASCII escaping handles even malformed Unicode conservatively and preserves
    # exact text, whitespace, roles, message order, and terminal status.
    return turn_id, json.dumps([status, content], separators=(',', ':')).encode('ascii'), chars


class SharedPrefixIndex:
    """An incremental, project-local index of exact shared conversation prefixes.

    Only hashes and turn references are retained. A shared prefix must contain at
    least two nonblank turns and 200 text characters. Divergent or uncertain
    content is never matched again later in that session.

    ``canonical`` maps duplicate references to the earliest retained reference.
    ``aliases`` maps each retained reference to its duplicate session/turn links.
    ``prefixes`` records the number of shared initial turns for each session.
    """

    def __init__(self):
        self.canonical: dict[TurnRef, TurnRef] = {}
        self.aliases: dict[TurnRef, list[dict]] = {}
        self.prefixes: dict[int, int] = {}
        self._prefixes: dict[bytes, TurnRef] = {}
        self._sessions: set[int] = set()
        self._session_turns: dict[int, tuple[str, ...]] = {}

    def add_session(self, session_id: int, turns: Iterable[dict]):
        if type(session_id) is not int or session_id <= 0:
            raise ValueError('Session ID must be a positive integer.')
        if session_id in self._sessions:
            raise ValueError('A session may only be added once to a shared-prefix index.')
        # Delay mutation until iteration succeeds. This also prevents repeated
        # content within the same session from matching itself.
        entries = []
        seen = set()
        prefix, chars, barrier = _INITIAL_HASH, 0, False
        for turn in turns:
            if barrier:
                continue
            content = _turn_content(turn)
            if content is None or content[0] in seen:
                barrier = True
                continue
            turn_id, encoded, count = content
            seen.add(turn_id)
            prefix = hashlib.sha256(prefix + encoded).digest()
            chars += count
            entries.append((prefix, turn_id, chars))

        shared = 0
        for digest, _, _ in entries:
            if digest not in self._prefixes:
                break
            shared += 1
        if shared < _MIN_TURNS or entries[shared - 1][2] < _MIN_CHARS:
            shared = 0
        self.prefixes[session_id] = shared
        # Resolve every position through the session matching the complete
        # accepted prefix. A global first-step match alone could otherwise
        # attach an isolated 'do' to an unrelated conversation.
        origin_session = self._prefixes[entries[shared - 1][0]][0] if shared else None
        for index, (digest, turn_id, _) in enumerate(entries):
            ref = (session_id, turn_id)
            if index < shared:
                original = (origin_session, self._session_turns[origin_session][index])
                # Values normally point directly to retained turns. Resolving
                # here makes that invariant explicit for nested forks.
                original = self.canonical.get(original, original)
                self.canonical[ref] = original
                self.aliases.setdefault(original, []).append({'session_id': session_id, 'turn_id': turn_id})
            self._prefixes.setdefault(digest, ref)
        self._session_turns[session_id] = tuple(turn_id for _, turn_id, _ in entries)
        self._sessions.add(session_id)
