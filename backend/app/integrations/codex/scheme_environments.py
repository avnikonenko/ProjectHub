"""Read a bounded subset of Conda environment declarations, without loading YAML.

Only block dependency lists and their nested pip lists are supported. This is
deliberately not a general YAML parser: aliases, tags, inline collections and
other installers are reported rather than interpreted. No channel/prefix/variable
values are returned, and no repository code or package manager is run.
"""
from pathlib import PurePosixPath
import re


_NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,159}')
_SPEC = re.compile(r'^([A-Za-z0-9_][A-Za-z0-9_.-]*)(.*)$')
_PIP = re.compile(r'^([A-Za-z0-9][A-Za-z0-9_.-]*)(\[[A-Za-z0-9_,.-]+\])?(.*)$')
_LIMITATION = 'Some Conda environment dependency declarations use unsupported syntax and were omitted.'


def is_environment_manifest(path):
    name = PurePosixPath(path).name.lower()
    if not name.endswith(('.yml', '.yaml')):
        return False
    stem = name.rsplit('.', 1)[0]
    return (stem in {'env', 'environment', 'conda'}
            or stem.startswith(('env_', 'env-', 'environment_', 'environment-', 'conda_', 'conda-'))
            or stem.endswith(('_env', '-env', '_environment', '-environment')))


def _scalar(value):
    value = value.strip()
    # Selectors carry platform conditions that this minimal model cannot retain.
    if re.search(r'\s+#\s*\[', value):
        return None
    if value.startswith(('"', "'")):
        # Simple quoted scalars only; do not implement YAML escape semantics.
        match = re.fullmatch(r'''(["'])([^\\\r\n]*?)\1(?:\s+#.*)?''', value)
        if not match or match[1] in match[2]:
            return None
        return match[2]
    value = value.split(' #', 1)[0].rstrip()
    if not value or value.startswith(('!', '&', '*', '[', '{', '|', '>')):
        return None
    return value


def environment_dependencies(value, limitations, pip_requirements=None):
    """Return (name, constraint, category) for literal supported declarations."""
    lines = value.splitlines()
    headers = [i for i, line in enumerate(lines) if re.match(r'^dependencies\s*:', line)]
    # Multiple documents or duplicate keys cannot safely establish one environment.
    if len(headers) != 1 or any(line.strip() in {'---', '...'} for line in lines):
        limitations.add(_LIMITATION)
        return []
    start = headers[0]
    if lines[start].split(':', 1)[1].split('#', 1)[0].strip():
        limitations.add(_LIMITATION)
        return []
    result, list_indent, pip_indent, pip_list_indent, skipped_indent = [], None, None, None, None
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        indent = len(line) - len(line.lstrip(' '))
        text = line[indent:]
        if indent == 0 and not text.startswith('-'):
            break
        if '\t' in line[:len(line) - len(line.lstrip())]:
            limitations.add(_LIMITATION)
            continue
        match = re.fullmatch(r'-\s+(.+)', text)
        if not match:
            limitations.add(_LIMITATION)
            continue
        if list_indent is None:
            list_indent = indent
        if skipped_indent is not None and indent > skipped_indent:
            continue
        if indent <= (skipped_indent if skipped_indent is not None else -1):
            skipped_indent = None
        entry = match[1].strip()
        if indent == list_indent:
            pip_indent = None
            pip_list_indent = None
            if re.fullmatch(r'pip\s*:\s*(?:#.*)?', entry):
                pip_indent = indent
                continue
            if ':' in entry and '::' not in entry:
                # Do not reinterpret packages nested beneath an unsupported installer.
                limitations.add(_LIMITATION)
                skipped_indent = indent
                continue
            category = 'environment:conda'
        elif pip_indent is not None and indent > pip_indent:
            if pip_list_indent is None:
                pip_list_indent = indent
            elif indent != pip_list_indent:
                limitations.add(_LIMITATION)
                continue
            category = 'environment:pip'
        else:
            limitations.add(_LIMITATION)
            continue
        scalar = _scalar(entry)
        if scalar is None:
            limitations.add(_LIMITATION)
            continue
        if category == 'environment:conda':
            if '::' in scalar:
                channel, scalar = scalar.split('::', 1)
                if not _NAME.fullmatch(channel):
                    limitations.add(_LIMITATION)
                    continue
            spec = _SPEC.fullmatch(scalar)
            if not spec or not _NAME.fullmatch(spec[1]) or (
                    spec[2] and not re.fullmatch(r'\s*[=<>!~][A-Za-z0-9_.*,+!=<>|~ -]*', spec[2])):
                limitations.add(_LIMITATION)
                continue
            result.append((spec[1], spec[2].strip(), category))
        else:
            if pip_requirements is not None and re.fullmatch(r'(?:-r\s*|--requirement(?:\s+|=)).+', scalar):
                pip_requirements.append(scalar)
                continue
            spec = _PIP.fullmatch(scalar)
            if not spec or (spec[3].strip() and not spec[3].strip().startswith(('=', '<', '>', '!', '~', ';', '@'))):
                limitations.add(_LIMITATION)
                continue
            result.append((spec[1], (spec[2] or '') + spec[3].strip(), category))
    return result
