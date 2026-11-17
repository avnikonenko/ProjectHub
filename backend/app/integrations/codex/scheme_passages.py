"""Deterministic whole-passage selection; all quoted spans remain original text."""
import re

from .knowledge_sources import _messages

READ_CHARS = 65_536
EXCERPT_CHARS = 6000
GAP = '\n\n[... omitted source text ...]\n\n'
_TOPICS = (
    r'\barchitectur\w*\b|\bcomponents?\b|\bmodules?\b',
    r'\bworkflow\w*\b|\bpipeline\w*\b|\bprocessing stages?\b',
    r'\bvalidat\w*\b|\breport\w*\b|\bprovenance\b',
    r'\binputs?\b|\boutputs?\b|\bhandoff\b|\bexport\w*\b',
    r'\bbackend\w*\b|\bdependencies\b|\bintegrat\w*\b',
    r'\brequirement\w*\b|\bconservative\b|\bretention\b|\bintegrity\b',
)
_PATTERNS = [re.compile(pattern, re.I) for pattern in _TOPICS]


def _passage_ranges(value):
    """Keep fenced diagrams/code atomic, including internal blank lines."""
    fences, opened, offset = [], None, 0
    for line in value.splitlines(True):
        marker = re.match(r'^[ \t]*(`{3,}|~{3,})([^\n]*)', line)
        if marker:
            delimiter = marker[1]
            if opened is None:
                opened = (offset, delimiter[0], len(delimiter))
            elif (delimiter[0] == opened[1] and len(delimiter) >= opened[2]
                  and not marker[2].strip()):
                fences.append((opened[0], offset + len(line), True))
                opened = None
        offset += len(line)
    if opened is not None:
        fences.append((opened[0], len(value), False))
    ranges, cursor = [], 0
    for left, right, complete in fences:
        ranges.extend((cursor + match.start(), cursor + match.end()) for match in re.finditer(
            r'\S[\s\S]*?(?=\n[ \t]*\n|\Z)', value[cursor:left]))
        if complete:
            ranges.append((left, right))
        cursor = right
    ranges.extend((cursor + match.start(), cursor + match.end()) for match in re.finditer(
        r'\S[\s\S]*?(?=\n[ \t]*\n|\Z)', value[cursor:]))
    return ranges, bool(opened)


def select_passages(value, limit=EXCERPT_CHARS):
    """Select exact complete paragraphs/diagrams, including their message context.

    No summary, sentence cutting, invented chronology or inferred acceptance.
    Oversized paragraphs are omitted rather than represented as complete sections.
    """
    ranges, incomplete_fence = _passage_ranges(value)
    if len(value) <= limit and not incomplete_fence:
        return {'text': value, 'spans': [{'start': 0, 'end': len(value)}], 'omitted': False}
    roles = []
    try:
        roles = [(start, start + len(body), role, ordinal)
                 for ordinal, role, body, start in _messages(value)]
    except ValueError:
        pass  # Plain notes or ambiguous transcript markers do not establish a speaker.
    blocks = []
    for start, end in ranges:
        body = value[start:end]
        role = next(((speaker, order) for left, right, speaker, order in roles
                     if left <= start < right), None)
        score = sum(bool(pattern.search(body)) for pattern in _PATTERNS)
        # A whole textual diagram preserves operation order and branching.
        if re.search(r'[│┌┐└┘▼→]|-->|=>', body):
            score += 5
        if len(re.findall(r'(?m)^\s*\d+[.)]\s+', body)) > 1:
            score += 2
        blocks.append({'start': start, 'end': end, 'role': role, 'score': score})
    chosen, seen = [], set()

    def rendered(indexes):
        ordered = sorted(indexes)
        ranges = []
        for index in ordered:
            block = blocks[index]
            if ranges and not value[ranges[-1]['end']:block['start']].strip():
                ranges[-1]['end'] = block['end']
            else:
                ranges.append({'start': block['start'], 'end': block['end']})
        return GAP.join(value[span['start']:span['end']] for span in ranges), ranges

    for index in sorted(range(len(blocks)), key=lambda i: (-blocks[i]['score'], i)):
        if len(chosen) >= 12:
            break
        block = blocks[index]
        identity = (block['role'], value[block['start']:block['end']])
        if identity in seen:
            continue
        text, _ = rendered([*chosen, index])
        if len(text) <= limit:
            chosen.append(index)
            seen.add(identity)
    # Small neighboring blocks retain headings/questions adjacent to selected content.
    # Selection above always wins over context when the overall cap is tight.
    for index in sorted(chosen.copy()):
        for neighbor in (index - 1, index + 1):
            if 0 <= neighbor < len(blocks) and neighbor not in chosen:
                block = blocks[neighbor]
                identity = (block['role'], value[block['start']:block['end']])
                if identity in seen:
                    continue
                if block['end'] - block['start'] <= 500:
                    text, _ = rendered([*chosen, neighbor])
                    if len(text) <= limit:
                        chosen.append(neighbor)
                        seen.add(identity)
    text, spans = rendered(chosen)
    # Record exact speaker boundaries even when one retained span crosses messages.
    for span in spans:
        span['messages'] = [{'start': max(left, span['start']), 'end': min(right, span['end']),
                             'role': role, 'message_order': order}
                            for left, right, role, order in roles
                            if left < span['end'] and right > span['start']]
    return {'text': text, 'spans': spans, 'omitted': True}
