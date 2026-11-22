"""Bounded lexical ownership for focused JavaScript and TypeScript call excerpts."""

from bisect import bisect_left, bisect_right
from heapq import heappop, heappush
import re


_IDENTIFIER = r'[A-Za-z_$][\w$]*'
_TYPE = r'(?::\s*[\w$.\[\]<>|&?, ]{1,120})?'
_TYPE_PARAMETERS = r'(?:<[^<>{}\n]{1,120}>\s*)?'
_FUNCTION = re.compile(
    rf'(?<![\w$.])function(?![\w$])\s*\*?\s*(?:(?P<name>{_IDENTIFIER})\s*)?'
    rf'{_TYPE_PARAMETERS}(?P<args>\()'
)
_ARROW_BINDING = re.compile(
    rf'(?<![\w$.])(?:const|let|var)\s+(?P<name>{_IDENTIFIER})'
    rf'{_TYPE}\s*=\s*(?:async(?=\s|\(|<)\s*)?{_TYPE_PARAMETERS}(?P<params>\(|{_IDENTIFIER})'
)
_ARROW = re.compile(rf'{_TYPE}\s*(?P<arrow>=>)')
_BODY = re.compile(rf'{_TYPE}\s*(?P<body>{{)')
_METHOD = re.compile(rf'(?<![\w$.])(?P<name>{_IDENTIFIER})\s*{_TYPE_PARAMETERS}(?P<args>\()')
_CONTROL = {'catch', 'for', 'if', 'switch', 'while', 'with'}
_OPEN = '([{'
_CLOSE = ')]}'


def _delimiter_pairs(text):
    openings, closings, stack = {}, {}, []
    for index, char in enumerate(text):
        if char in _OPEN:
            stack.append((index, char))
        elif char in _CLOSE:
            if not stack or _OPEN.index(stack[-1][1]) != _CLOSE.index(char):
                return None, None
            opening, _ = stack.pop()
            openings[opening] = index
            closings[index] = opening
    # Unclosed delimiters are left unmatched so a truncated body can be skipped.
    return openings, closings


def _expression_end(text, start, openings):
    index = start
    while index < len(text) and text[index] not in ',;)]}':
        if text[index] in _OPEN and index not in openings:
            return len(text)
        index = openings.get(index, index) + 1
    return index


def focused_call_owner(masked):
    """Return the proved function or module owner of a call-position offset.

    The input must preserve offsets while blanking comments and literals.
    This intentionally recognizes only bounded callable forms. Anonymous or
    ambiguous call sites never inherit the nearest named function.
    """
    openings, closings = _delimiter_pairs(masked)
    if openings is None:
        return lambda _: None
    spans = []
    claimed_bodies = set()

    def add_body(start, body, name):
        claimed_bodies.add(body)
        spans.append((start, body + 1, None))
        end = openings.get(body)
        spans.append((body + 1, len(masked) if end is None else end, name if end is not None else None))

    for head in _FUNCTION.finditer(masked):
        end = openings.get(head.start('args'))
        body = _BODY.match(masked, end + 1) if end is not None else None
        if body:
            add_body(head.start(), body.start('body'), head['name'])
        elif end is None:
            spans.append((head.start(), len(masked), None))
        else:
            spans.append((head.start(), end + 1, None))

    bound_arrows = {}
    for head in _ARROW_BINDING.finditer(masked):
        if head['params'] == '(':
            end = openings.get(head.start('params'))
            if end is None:
                continue
        else:
            end = head.end('params') - 1
        arrow = _ARROW.match(masked, end + 1)
        if arrow:
            bound_arrows[arrow.start('arrow')] = (head.start(), head['name'])

    for arrow in re.finditer(r'=>\s*', masked):
        body = arrow.end()
        head_end = arrow.start() - 1
        while head_end >= 0 and masked[head_end].isspace():
            head_end -= 1
        start, name = bound_arrows.get(arrow.start(), (closings.get(head_end, arrow.start()), None))
        if body < len(masked) and masked[body] == '{':
            add_body(start, body, name)
        else:
            end = _expression_end(masked, body, openings)
            if name and end < len(masked) and masked[end] == ';' and '\n' not in masked[body:end]:
                # A named, explicitly terminated single-line expression has a
                # clear body. Avoid guessing where automatic semicolons apply.
                spans.extend(((start, body, None), (body, end, name)))
            else:
                spans.append((start, end, None))

    # Method-like syntax may introduce an execution scope, even when its form
    # is outside the set of supported named functions. Do not assign its body
    # to an enclosing function, or mistake the method head for a call.
    for head in _METHOD.finditer(masked):
        if head['name'] in _CONTROL:
            continue
        end = openings.get(head.start('args'))
        body = _BODY.match(masked, end + 1) if end is not None else None
        if body and body.start('body') not in claimed_bodies:
            add_body(head.start(), body.start('body'), None)

    # Index each change in ownership. Later starting spans take precedence,
    # which keeps nested functions and unsupported callbacks distinct.
    ordered = sorted((start, end, order, owner) for order, (start, end, owner) in enumerate(spans) if start < end)
    boundaries = sorted({position for start, end, _, _ in ordered for position in (start, end)})
    owners, active, nesting, next_span = [], [], [], 0
    module = object()
    for boundary in boundaries:
        while nesting and nesting[-1][0] <= boundary:
            nesting.pop()
        while next_span < len(ordered) and ordered[next_span][0] == boundary:
            start, end, order, owner = ordered[next_span]
            if owner is not None:
                if nesting:
                    owner = nesting[-1][1] + '.' + owner
                nesting.append((end, owner))
            # A nested anonymous expression may have exactly the enclosing
            # expression's bounds. An unproved owner wins that tie.
            heappush(active, (-start, end, owner is not None, order, owner))
            next_span += 1
        while active and active[0][1] <= boundary:
            heappop(active)
        owners.append(active[0][4] if active else module)

    brace_starts = [match.start() for match in re.finditer(r'\{', masked)]
    brace_ends = [index for index in closings if masked[index] == '}']

    def owner_at(position):
        index = bisect_right(boundaries, position)
        owner = owners[index - 1] if index else module
        if owner is not module:
            return owner
        if bisect_right(brace_starts, position) == bisect_left(brace_ends, position):
            return 'module'
        return None

    return owner_at
