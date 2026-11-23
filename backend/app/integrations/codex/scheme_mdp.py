"""Extract selected settings from a GROMACS simulation template."""

from collections import Counter
import re


_ENUMS = {
    'integrator': frozenset({'md', 'md-vv', 'md-vv-avek', 'sd', 'bd', 'steep', 'cg', 'l-bfgs', 'nm'}),
    'tcoupl': frozenset({'no', 'berendsen', 'nose-hoover', 'andersen', 'andersen-massive', 'v-rescale'}),
    'pcoupl': frozenset({'no', 'berendsen', 'parrinello-rahman', 'mttk', 'c-rescale'}),
    'pcoupltype': frozenset({'isotropic', 'semiisotropic', 'anisotropic', 'surface-tension'}),
    'constraints': frozenset({'none', 'h-bonds', 'all-bonds', 'h-angles', 'all-angles'}),
    'continuation': frozenset({'yes', 'no'}),
    'gen-vel': frozenset({'yes', 'no'}),
    'cutoff-scheme': frozenset({'verlet', 'group'}),
    'coulombtype': frozenset({'cut-off', 'reaction-field', 'pme', 'ewald', 'p3m-ad'}),
}
_NUMERIC_LISTS = frozenset({'ref-t', 'ref-p'})
_KEYS = _ENUMS.keys() | _NUMERIC_LISTS | {'dt', 'nsteps'}
_NUMBER = re.compile(r'[+-]?(?:[0-9]{1,18}(?:\.[0-9]{0,18})?|\.[0-9]{1,18})(?:[eE][+-]?[0-9]{1,3})?')
_INTEGER = re.compile(r'[+-]?[0-9]{1,18}')


def _setting_value(key: str, value: str) -> str:
    if key in _ENUMS:
        return value if value.lower() in _ENUMS[key] else ''
    if key == 'dt':
        return value if _NUMBER.fullmatch(value) else ''
    if key == 'nsteps':
        return value if _INTEGER.fullmatch(value) else ''
    parts = value.split()
    return ' '.join(parts) if 1 <= len(parts) <= 16 and all(_NUMBER.fullmatch(part) for part in parts) else ''


def mdp_excerpt(value: str, *, limit: int) -> str:
    """Show line-numbered template assignments within the excerpt budget."""
    rows = []
    occurrences = Counter()
    for number, line in enumerate(value.splitlines(), 1):
        key, separator, setting = line.partition(';')[0].partition('=')
        key = key.strip().lower().replace('_', '-')
        if not separator or key not in _KEYS:
            continue
        occurrences[key] += 1
        if normalized := _setting_value(key, setting.strip()):
            rows.append((number, key, normalized))

    excerpt, size = [], 0
    for number, key, setting in rows:
        suffix = ' (repeated setting in template)' if occurrences[key] > 1 else ''
        line = f'L{number}: MDP template {key} = {setting}{suffix}'
        line_size = len(line) + bool(excerpt)
        if size + line_size > limit:
            break
        excerpt.append(line)
        size += line_size
    return '\n'.join(excerpt)
