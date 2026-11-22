"""Line-numbered section declarations from bounded namelist inputs."""

import re


_GROUP = re.compile(r'(?:&([A-Za-z][A-Za-z0-9_]{0,63})|/)(?:\s*[#!].*)?$', re.ASCII)


def namelist_excerpt(value: str, *, limit: int) -> str:
    """Show complete active and commented sections, without copying their values."""
    if limit < 1:
        return ''

    active = None
    commented = None
    active_groups = []
    commented_groups = []
    for number, line in enumerate(value.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        comment = line[0] in '#!'
        directive = _GROUP.fullmatch(line[1:].lstrip() if comment else line)
        if comment and active is not None:
            continue
        if not comment:
            commented = None
        if directive is None:
            continue
        name = (directive[1] or 'end').lower()
        if name == 'end':
            if comment and commented is not None:
                commented_groups.append(commented)
                commented = None
            elif not comment:
                if active is None:
                    return ''
                active_groups.append(active)
                active = None
        elif comment:
            commented = number, name
        elif active is not None:
            return ''
        else:
            active = number, name

    if active is not None or not active_groups:
        return ''
    rows = sorted(
        [(number, f'Active namelist section &{name}') for number, name in active_groups]
        + [(number, f'Commented-out namelist section &{name}') for number, name in commented_groups]
    )
    excerpt, size = [], 0
    for number, description in rows:
        line = f'L{number}: {description}'
        line_size = len(line) + bool(excerpt)
        if size + line_size > limit:
            break
        excerpt.append(line)
        size += line_size
    return '\n'.join(excerpt)
