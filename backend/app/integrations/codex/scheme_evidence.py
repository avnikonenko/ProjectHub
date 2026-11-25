"""Bounded Scheme inputs from local files and stored records; never runs project code or AI."""
import ast
from collections import Counter, deque
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import PurePosixPath
import re
import stat
import time
import tokenize
import tomllib

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from ...config import DEFAULT_SCHEME_PACKAGE_CHARS, DEFAULT_SCHEME_READ_BYTES, scheme_inspection_limits
from ...models import Project, ProjectHistoryBatch
from ...project_evidence import (
    EvidenceReadError, _CODE_PRIVATE, _code_category, _code_order, _digest,
    _code_hints, _excluded, _exclusions, _git, _repository_root,
)
from ...services import require_project
from .history_search import _SHARE_REFERENCE
from .knowledge_sources import CONVERSATION_NOTE_SQL
from .scheme_passages import GAP, READ_CHARS, EXCERPT_CHARS, select_passages
from .scheme_environments import environment_dependencies, is_environment_manifest
from .scheme_js_scope import focused_call_owner, focused_ui_declarations
from .scheme_knowledge import collect_published_knowledge
from .scheme_local_handoffs import local_handoff_rows
from .scheme_mdp import mdp_excerpt
from .scheme_migration_sql import compact_migration_sql, migration_sql_declarations
from .scheme_named_files import named_file_excerpt, named_file_kind
from .scheme_namelist import namelist_excerpt
from .scheme_python_dependencies import local_python_modules, python_imports, setup_dependencies
from .scheme_python_flow import (python_entrypoint_trace, python_flow_declarations, python_imported_symbols,
                                 python_route_return_calls)
from .scheme_route_evidence import python_route_declarations
from .scheme_cli_choices import cli_choice_rows
from .scheme_workflow_sql import workflow_sql_rows
from .scheme_workflow_guides import is_workflow_alternative

_MAX_PATHS = 300
_MAX_BYTES = 65536
_MAX_ENTRYPOINT_FILE_BYTES = 192 * 1024
_MAX_DEPENDENCY_FILE_BYTES = 2 * 1024 * 1024
_MAX_DEPENDENCY_READ_BYTES = 32 * 1024 * 1024
_DEPENDENCY_INSPECTION_SECONDS = 10.0
_INSPECTION_SECONDS = 5.0
_MAX_METADATA_BYTES = 1024 * 1024
_METADATA_SECONDS = 5.0
_MAX_EXCERPT = 1600
_MAX_DEPENDENCIES = 100
_MAX_RECORDED_FILE_OMISSIONS = 100
_REQUIREMENT = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9_,. -]+\])?\s*([<>=!~;@].*|\(.*\))?$')

_SCOPE_WORD = re.compile(r'[^\W_]+')
_SCOPE_CAMEL_CASE = re.compile(r'(?<=[a-z0-9])(?=[A-Z])')
_SCOPE_PATH_TOKEN = re.compile(r'([`"])([\w./ -]+)\1|(?<![\w./-])(/?(?:[\w.-]+/)*[\w.-]+)')
_LIKELY_FILE_SUFFIXES = frozenset(
    '.c .cpp .css .csv .go .h .html .in .ini .java .js .json .jsx .md .mjs .pdf .png .py .rb .rs .rst .sh .sql '
    '.toml .ts .tsx .txt .xml .yaml .yml .zip'.split())
_SCOPE_STOP_WORDS = frozenset(
    'about also both can code could describe diagram explain from give have how include into just '
    'like make only part please project scheme see show should that the their them there these they '
    'this those through want what when where which will with would your'.split())
_SCOPE_VARIANTS = {
    'cach': 'cache caches cached caching cacheable cachable'.split(),
    'cancel': 'cancel cancels canceled cancelled canceling cancelling cancellation'.split(),
    'retriev': 'retrieve retrieves retrieved retrieving retrieval retrievals retrievable'.split(),
    'validat': 'validate validates validated validating validation validations validator validators'.split(),
    'configur': 'configure configures configured configuring configuration configurations configurable'.split(),
    'execut': 'execute executes executed executing execution executions executable executables'.split(),
    'save': 'save saves saved saving'.split(),
    'serializ': 'serialize serializes serialized serializing serialization serializer serializers'.split(),
    'polic': 'policy policies'.split(),
}
_SCOPE_VARIANT_ROOTS = {word: root for root, words in _SCOPE_VARIANTS.items() for word in words}


def _scope_tokens(value):
    for part in _SCOPE_WORD.findall(_SCOPE_CAMEL_CASE.sub(' ', value).casefold()):
        if (len(part) >= 3 or part in {'db', 'ci', 'ui'}) and not part.isdecimal() and part not in _SCOPE_STOP_WORDS:
            yield part


def _scope_root(word):
    if word in _SCOPE_VARIANT_ROOTS:
        return _SCOPE_VARIANT_ROOTS[word]
    for suffix in ('able', 'ing', 'ed'):
        if len(word) - len(suffix) >= 4 and word.endswith(suffix):
            return word[:-len(suffix)]
    if word.endswith(('ches', 'shes', 'sses', 'xes', 'zzes')) and len(word) >= 6:
        return word[:-2]
    if word.endswith('s') and not word.endswith(('ss', 'us', 'is')) and len(word) >= 5:
        return word[:-1]
    return word


class _EvidenceScope:
    def __init__(self, value):
        self.value = ' '.join(value.split())
        tokens = tuple(dict.fromkeys(_scope_tokens(self.value)))
        self.terms = frozenset(list(dict.fromkeys(map(_scope_root, tokens)))[:24])
        self.exact_terms = frozenset(token for token in tokens if _scope_root(token) in self.terms)
        names = ((match[2] or match[3]).strip().rstrip('.').removeprefix('./')
                 for match in _SCOPE_PATH_TOKEN.finditer(value))
        self.paths = {name.casefold(): name for name in names
                      if name and ('.' in PurePosixPath(name).name or named_file_kind(name)
                                   or _scheme_category(name) == 'docs')}
        self.excluded_references = set()
        self.preferred_basenames = {}

    def prefer_tracked_basenames(self, paths, tracked):
        for path in paths:
            name = PurePosixPath(path.casefold()).name
            if name in self.paths and path in tracked:
                self.preferred_basenames.setdefault(name, set()).add(path)
        for matches in self.preferred_basenames.values():
            production = {path for path in matches if not _nonproduction_copy(path)}
            if production:
                matches.intersection_update(production)

    def shadowed_basename(self, path):
        folded = path.casefold()
        preferred = self.preferred_basenames.get(PurePosixPath(folded).name)
        return bool(preferred and path not in preferred and not ('/' in path and folded in self.paths))

    def score(self, value):
        if not self.terms:
            return 0
        hits = {}
        for token in _scope_tokens(value):
            root = _scope_root(token)
            if root in self.terms:
                hits[root] = max(hits.get(root, 0), 3 if token in self.exact_terms else 2)
        return sum(hits.values())

    def path_rank(self, path):
        if self.shadowed_basename(path):
            return 0, 0
        return int(bool(self.matching_references(path))), self.score(path)

    def matching_references(self, path):
        folded = path.casefold()
        return self.paths.keys() & {folded, PurePosixPath(folded).name}

    def unresolved_references(self, matched_paths, repository_available):
        resolved = {key for path in matched_paths for key in self.matching_references(path)}
        for key, name in self.paths.items():
            if key in resolved:
                continue
            if key in self.excluded_references:
                reason = 'excluded_by_policy'
            elif (named_file_kind(name) or PurePosixPath(name).suffix.lower() in _LIKELY_FILE_SUFFIXES
                  or PurePosixPath(name).name.lower().startswith('.env') or _scheme_category(name) == 'docs'):
                reason = 'outside_repository_inventory' if repository_available else 'repository_unavailable'
            else:
                continue  # A dotted identifier such as module.function can also be a search term.
            yield {'path': name, 'reason': reason}

    def source_key(self, source, *, structural_evidence=False):
        path = source.get('path', '')
        exact, path_score = self.path_rank(path) if path else (0, 0)
        return (source['id'] != 'project:objective', -exact, _historical_path(path), not structural_evidence,
                -self.score(source['text']), -path_score, -self.score(source['label']))

    @property
    def fts_query(self):
        expressions = []
        for term in sorted(self.terms):
            variants = _SCOPE_VARIANTS.get(term)
            if variants and term not in self.exact_terms:
                expressions.extend(f'"{variant}"' for variant in variants)
            else:
                expressions.append(f'"{term}"*')
        return ' OR '.join(expressions)


def _source(identifier, label, value, provenance, **metadata):
    return {'id': identifier, 'label': label[:240], 'text': value,
            'provenance': provenance, 'content_hash': hashlib.sha256(value.encode()).hexdigest(), **metadata}


def _knowledge_closure(result):
    """Budgeting must not leave an interpretation without its original evidence."""
    sources = result['sources']
    primary_ids = {source['id'] for source in sources
                   if source.get('provenance') != 'published Knowledge interpretation'}
    kept = [source for source in sources if source.get('provenance') != 'published Knowledge interpretation'
            or source.get('supporting_source_ids') and set(source['supporting_source_ids']) <= primary_ids]
    removed = len(sources) - len(kept)
    sources[:] = kept
    context = result.get('knowledge_context')
    if isinstance(context, dict):
        interpretations = [source for source in sources if source.get('provenance') == 'published Knowledge interpretation']
        context['included_items'] = len(interpretations)
        context['omitted_items'] = max(0, context.get('available_items', 0) - context['included_items'])
        if 'input_chars' in context:
            context['input_chars'] = sum(len(source['text']) for source in sources
                if source.get('provenance') in {'published Knowledge interpretation', 'published Knowledge original evidence'})
        if 'current_evidence_availability' in context:
            availability = {}
            for source in interpretations:
                state = source.get('evidence_availability', 'unknown')
                availability[state] = availability.get(state, 0) + 1
            context['current_evidence_availability'] = availability
        if 'partial_evidence_items' in context:
            context['partial_evidence_items'] = sum(source.get('evidence_partial') is True for source in interpretations)
    if removed:
        coverage = result['coverage']
        coverage['partial'] = True
        coverage['limitations'] = sorted({*coverage['limitations'],
            'Some Knowledge interpretations were omitted because their original evidence did not fit the input budget.'})
        coverage['omitted_reason_counts']['knowledge_support_budget'] = removed


def _bound_package(result, max_chars=DEFAULT_SCHEME_PACKAGE_CHARS):
    """Bound serialized metadata too, keeping whole sources and valid references."""
    def update_file_counts():
        counts = result['coverage'].get('file_counts')
        if counts is not None:
            counts['evidence_files'] = len({source['path'] for source in result['sources'] if source.get('path')})
            counts['read_not_in_evidence'] = counts['read_files'] - counts['evidence_files']

    def serialized_size(value):
        return len(json.dumps(value, ensure_ascii=False))

    _knowledge_closure(result)
    update_file_counts()
    payload = {key: result[key] for key in ('sources', 'dependencies', 'tree', 'coverage', 'knowledge_context') if key in result}
    used = serialized_size(payload)
    if used > max_chars:
        coverage = result['coverage']
        coverage['partial'] = True
        coverage['limitations'] = sorted({*coverage['limitations'],
            'The serialized evidence package budget was reached; some structure or source evidence was omitted.'})
        counts = coverage['omitted_reason_counts']
        counts['package_output_budget'] = 0
        used = serialized_size(payload)

        def remove(items, index=-1):
            nonlocal used
            value = items.pop(index)
            used -= serialized_size(value) + (2 if items else 0)
            counts['package_output_budget'] += 1
            return value

        # Structure is supplemental; preserve priority-ordered source evidence first.
        # Reserve a little space for the updated counters and coverage metadata.
        while used > max(0, max_chars - 512):
            if result['tree']:
                remove(result['tree'])
            elif result['sources']:
                removed = remove(result['sources'])
                for index in range(len(result['dependencies']) - 1, -1, -1):
                    if removed['id'] in result['dependencies'][index]['source_ids']:
                        remove(result['dependencies'], index)
            else:
                break
        _knowledge_closure(result)
        coverage['conversation_excerpts'] = sum(
            source['provenance'] == 'Codex conversation' for source in result['sources'])
    update_file_counts()
    return serialized_size(payload)


def _safe_relative(value, patterns):
    path = PurePosixPath(value)
    return (bool(value) and not path.is_absolute() and '..' not in path.parts
            and not any(c < '\x20' or '\ud800' <= c <= '\udfff' for c in value)
            and not _excluded(value, patterns))


def _manifest(path):
    relative = PurePosixPath(path.lower())
    name = relative.name
    return is_environment_manifest(path) or name in ('pyproject.toml', 'package.json', 'pipfile') or (
        (name.startswith('requirements') or 'requirements' in relative.parts[:-1])
        and name.endswith(('.txt', '.in', '.pip')))


def _descriptor(root_fd, relative):
    """Open each component relative to the root without following symlinks."""
    opened = []
    try:
        current = root_fd
        for part in PurePosixPath(relative).parts[:-1]:
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            opened.append(current)
        fd = os.open(PurePosixPath(relative).name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=current)
        opened.append(fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError('Not a regular file')
        return os.dup(fd)
    finally:
        for fd in reversed(opened):
            os.close(fd)


class _InspectionStopped(Exception):
    def __init__(self, reason):
        self.reason = reason


class _InspectionBudget:
    def __init__(self, max_bytes=DEFAULT_SCHEME_READ_BYTES, seconds=None):
        self.started = time.monotonic()
        self.deadline = self.started + (_INSPECTION_SECONDS if seconds is None else seconds)
        self.bytes_read = 0
        self.max_bytes = max_bytes
        self.prefix_read_files = 0

    def check(self):
        if time.monotonic() >= self.deadline:
            raise _InspectionStopped('time_budget')
        if self.bytes_read >= self.max_bytes:
            raise _InspectionStopped('byte_budget')


def _read(root_fd, relative, *, allow_prefix=False, budget=None, max_file_bytes=None, python_encoding=False):
    max_file_bytes = _MAX_BYTES if max_file_bytes is None else max_file_bytes
    if budget is not None:
        budget.check()
    fd = _descriptor(root_fd, relative)
    try:
        data = bytearray()
        while len(data) <= max_file_bytes:
            maximum = min(8192, max_file_bytes + 1 - len(data))
            if budget is not None:
                budget.check()
                maximum = min(maximum, budget.max_bytes - budget.bytes_read)
            chunk = os.read(fd, maximum)
            if budget is not None:
                budget.bytes_read += len(chunk)
                if time.monotonic() >= budget.deadline:
                    raise _InspectionStopped('time_budget')
            if not chunk:
                break
            data.extend(chunk)
            # An exact-budget file can already be complete. Confirm EOF using
            # the open descriptor without spending another byte of the budget.
            if budget is not None and budget.bytes_read == budget.max_bytes and len(data) == os.fstat(fd).st_size:
                break
        if b'\0' in data:
            raise ValueError('Binary file')
        prefix = len(data) > max_file_bytes
        if prefix:
            if not allow_prefix:
                raise ValueError('Oversized file')
            data = data[:max_file_bytes].rsplit(b'\n', 1)[0]
        encoding = tokenize.detect_encoding(BytesIO(data).readline)[0] if python_encoding else 'utf-8'
        value = data.decode(encoding)
        if prefix and budget is not None:
            budget.prefix_read_files += 1
        return value
    finally:
        os.close(fd)


def _markers(root_fd, candidates):
    result = []
    deadline = time.monotonic() + _METADATA_SECONDS
    size = 0
    for relative in candidates:
        if time.monotonic() >= deadline:
            return result, 'time_budget'
        # Reserve enough room for the bounded numeric stat fields as well as the path.
        size += len(relative.encode('utf-8', errors='surrogateescape')) + 256
        if size > _MAX_METADATA_BYTES:
            return result, 'byte_budget'
        try:
            fd = _descriptor(root_fd, relative)
            try:
                s = os.fstat(fd)
                marker = [s.st_ino, s.st_mtime_ns, s.st_ctime_ns, s.st_size, s.st_mode]
            finally:
                os.close(fd)
        except OSError:
            marker = 'unavailable'
        result.append([relative, marker])
    return result, 'time_budget' if time.monotonic() >= deadline else None


_AUXILIARY = {'script', 'scripts', 'migration', 'migrations', 'test', 'tests',
              'tool', 'tools', 'benchmark', 'benchmarks', 'example', 'examples', 'docs'}
_ENTRYPOINTS = {'main', 'app', 'index', 'server', 'cli', 'router', 'routes', 'services', 'models'}
_JS_EXTENSIONS = ('.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs')
_CODE_EXTENSIONS = ('.py', *_JS_EXTENSIONS)
_MIGRATION_SCOPE_WORDS = frozenset('database db fts fts5 index migration schema search sqlite table trigger'.split())
_MAX_FOCUSED_MIGRATIONS = 48
_MAX_FOCUSED_SCHEMA_SOURCES = 3
_WORKFLOW_ROUTE = re.compile(r'/(?:import|search|generate|generation|brief|scheme|publish)(?=[/ ?])')
_WORKFLOW_CONFIG = re.compile(r'(?<![\w.-])[\w.-]+\.(?:in|mdp)(?![\w.-])', re.I)
_VIRTUAL_INDEX = re.compile(r'(?m)^L\d+: CREATE VIRTUAL TABLE (\w+) USING fts[345]\b', re.I)
_INDEX_WRITE = re.compile(r'(?m)^L\d+: CREATE TRIGGERS? [^\n]+: [^\n]*\bINSERT INTO (\w+)\b', re.I)
_INDEX_QUERY = re.compile(r'(?m)^L\d+: SELECT FROM \w+ WHERE (\w+) MATCH\b', re.I)
_ROUTE_SCOPE_WORDS = frozenset('api click endpoint history link navigat open result route search transcript url'.split())
_MAX_FOCUSED_ROUTE_CHARS = 800
_MAX_FOCUSED_FLOW_CHARS = 850
_ROUTE_SOURCE_GROUPS = (
    (re.compile(r'(?m)^L\d+: (HTTP route .+)$'), 2),
    (re.compile(r'(?m)^L\d+: (\w+ builds navigation link .+)$'), 1),
    (re.compile(r'(?m)^L\d+: (Client navigation .+)$'), 2),
)
_FLOW_SOURCE_GROUPS = (
    (re.compile(r'(?m)^L\d+: ((?:UI click binding|Client request syntax) .+)$'), 2),
    (re.compile(r'(?m)^L\d+: (Python flow [^\n]+ under with [^\n]+: if [^\n]+ -> raise[^\n]*)$'), 2),
    (re.compile(r'(?m)^L\d+: (Python flow .+)$'), 4),
)
_JS_HASH_ROUTE = re.compile(
    r"""(?P<operation>\breturn\s+|\.startsWith\(\s*)(?P<quote>['"`])(?P<fragment>#[a-zA-Z][\w-]{0,39}\?)""")


def _python_migration(relative):
    path = PurePosixPath(relative.lower())
    return path.suffix == '.py' and bool({'migrations', 'alembic'} & set(path.parts[:-1]))


def _scoped_structural_source_refs(sources, focus):
    """Keep evidence from each handoff even when one file declares many routes."""
    selected = set()
    wants_history = bool(focus.terms & {'conversation', 'transcript', 'history'})
    groups = (*_ROUTE_SOURCE_GROUPS, *_FLOW_SOURCE_GROUPS) if focus.terms & _ROUTE_SCOPE_WORDS else _FLOW_SOURCE_GROUPS
    for pattern, limit in groups:
        ranked = []
        for source in sources:
            if source['provenance'] != 'observed in code':
                continue
            statements = pattern.findall(source['text'])
            score = focus.score('\n'.join(statements))
            if wants_history:
                history_links = sum('#history?' in statement for statement in statements)
                if history_links:
                    score += 2 + min(3, history_links)
            if score:
                ranked.append((-score, -max(map(focus.score, statements)), source['id']))
        selected.update(identifier for _, _, identifier in sorted(ranked)[:limit])
    return selected


def _scheme_category(relative):
    """Include project guides without changing other features' evidence policy."""
    path = PurePosixPath(relative)
    if path.suffix.lower() in {'.md', '.rst'} and any(
            part.casefold() in {'docs', 'documentation'} for part in path.parts[:-1]):
        return 'docs'
    return _code_category(relative)


def _production_path(relative):
    path = PurePosixPath(relative)
    return _scheme_category(relative) == 'code' and not any(
        part.casefold() in _AUXILIARY | {'archive', 'archives', 'backup', 'backups'}
        or part.startswith('.') or part.casefold().startswith('_changed_')
        for part in path.parts[:-1])


def _historical_path(relative):
    """A selection hint, not proof that an old file is unused."""
    return any(part.casefold() in {'archive', 'archives', 'backup', 'backups', 'old'}
               or part.casefold().startswith(('old_', '_changed_'))
               for part in PurePosixPath(relative).parts[:-1])


def _nonproduction_copy(relative):
    return _historical_path(relative) or any(
        part.casefold() in {'test', 'tests', 'fixtures', 'example', 'examples'}
        for part in PurePosixPath(relative).parts[:-1])


def _scheme_order(relative):
    path = PurePosixPath(relative)
    category = _scheme_category(relative)
    group = 0 if _manifest(relative) or path.name == 'setup.py' else 1 if category == 'docs' else 2 if _production_path(relative) else 3
    return group, path.stem.casefold() not in _ENTRYPOINTS, len(path.parts), relative.casefold(), relative


def _area(relative):
    parts = PurePosixPath(relative).parts
    return parts[0] if len(parts) > 1 else '.'


def _shared_feature_stems(paths):
    areas = {}
    for relative in paths:
        if _production_path(relative):
            stem = PurePosixPath(relative).stem.casefold()
            if stem not in _ENTRYPOINTS:
                areas.setdefault(stem, set()).add(_area(relative))
    # Selection hint only: matching names do not establish a dependency.
    return {stem for stem, locations in areas.items() if len(locations) > 1}


def _import_targets(value, relative, available, *, called_names=None):
    """Resolve explicit local imports; prioritize application entrypoints/feature use."""
    path = PurePosixPath(relative)
    bases, feature_names = [], set()
    if path.suffix.lower() == '.py':
        try:
            parsed = ast.parse(value)
        except (SyntaxError, ValueError, RecursionError):
            return []
        for node in ast.walk(parsed):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
                name = _qualified_name(node.value.func)
                if name:
                    feature_names.add(name.split('.')[0])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {'include_router', 'register_blueprint', 'use'}:
                feature_names.update(argument.id for argument in node.args if isinstance(argument, ast.Name))
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                bases.extend((PurePosixPath(*alias.name.split('.')), alias.asname or alias.name.split('.')[0]) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parent = path.parent
                    for _ in range(node.level - 1):
                        parent = parent.parent
                    base = parent / PurePosixPath(*(node.module or '').split('.'))
                else:
                    base = PurePosixPath(*(node.module or '').split('.'))
                for alias in node.names:
                    name = alias.asname or alias.name
                    bases.append((base, name))
                    if alias.name != '*':
                        bases.append((base / alias.name, name))
    else:
        masked = _mask_js(value)
        feature_names.update(re.findall(r'<([A-Z][A-Za-z0-9_]*)\b', masked))
        for match in re.finditer(r"(?:\bfrom\s*|\bimport\s*(?:\(\s*)?)['\"]([^'\"]{1,180})['\"]", value):
            if not masked[match.start():match.start() + 4].strip():
                continue
            target = match[1]
            if target.startswith('.') and not any(char in target for char in ('?', '#', ':')):
                parts = list(path.parent.parts)
                safe = True
                for part in PurePosixPath(target).parts:
                    if part == '..':
                        if parts:
                            parts.pop()
                        else:
                            safe = False
                            break
                    elif part != '.':
                        parts.append(part)
                if safe:
                    base = PurePosixPath(*parts)
                    bases.append((base, base.stem))
    found = {}
    for base, symbol in bases:
        if called_names is not None and symbol not in called_names:
            continue
        candidates = [base.as_posix(), *((str(base) + ext) for ext in _CODE_EXTENSIONS),
                      *((base / ('index' + ext)).as_posix() for ext in _CODE_EXTENSIONS),
                      (base / '__init__.py').as_posix()]
        for candidate in candidates:
            if candidate in available and candidate != relative:
                score = (symbol not in feature_names,
                         PurePosixPath(candidate).stem.casefold() not in {'router', 'routes', 'app', 'main', 'index', 'server'})
                found[candidate] = min(found.get(candidate, score), score)
    return sorted(found, key=lambda candidate: (*found[candidate], *_scheme_order(candidate)))


def _general_repository_order(sources, entrypoints, declared_modules, handoffs, reachable, configurations, guides, route_targets):
    """Give entry points, linked code, configurations and guides independent turns."""
    lanes = [deque() for _ in range(9)]
    for source in sources:
        path = source['path']
        lane = (1 if path in declared_modules and path in reachable else 0 if path in entrypoints
                else 1 if path in handoffs else 2 if path in configurations
                else 3 if path in guides else 4 if _manifest(path) else 5 if 'HTTP route ' in source['text']
                else 6 if path in route_targets else 7 if path in reachable else 8)
        lanes[lane].append(source)
    lanes[0] = deque(sorted(lanes[0], key=lambda source: declared_modules.get(source['path'], len(declared_modules))))
    lanes[1] = deque(sorted(lanes[1], key=lambda source: source['path'] not in declared_modules))
    order = []
    pending = deque(lane for lane in lanes if lane)
    while pending:
        lane = pending.popleft()
        order.append(lane.popleft())
        if lane:
            pending.append(lane)
    return order


def _maintained_indexes(text):
    return ({name.casefold() for name in _VIRTUAL_INDEX.findall(text)}
            & {name.casefold() for name in _INDEX_WRITE.findall(text)})


def index_handoff_tables(maintenance_text, query_text):
    """Identify tables verified by both an inspected index writer and query."""
    return _maintained_indexes(maintenance_text) & {name.casefold() for name in _INDEX_QUERY.findall(query_text)}


def _index_workflows(sources, preferred_paths=()):
    """Pair SQLite index maintenance with runtime queries on the same index."""
    for schema in sorted(sources, key=lambda source: source['path']):
        if not _python_migration(schema['path']):
            continue
        indexes = _maintained_indexes(schema['text'])
        if not indexes:
            continue
        matches = []
        for source in sources:
            if source is schema or source['provenance'] != 'observed in code':
                continue
            queried = {name.casefold() for name in _INDEX_QUERY.findall(source['text'])}
            if indexes & queried:
                matches.append((source['path'] in preferred_paths, len(indexes & queried), len(queried), source))
        if matches:
            matches.sort(key=lambda item: (-item[0], -item[1], -item[2]))
            yield [schema, matches[0][3]]


def _repository(repository_path, patterns, *, inventory=None, requested=None):
    if not repository_path:
        return None, [], [], ['No repository is linked; diagrams can use saved discussions only.']
    deadline = time.monotonic() + 5
    root = _repository_root(repository_path, deadline)
    listed, _ = _git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', deadline=deadline)
    paths = sorted({os.fsdecode(v) for v in listed.split(b'\0') if v}, key=_code_order)
    listed_count = len(paths)
    # Git's exclude rules apply to untracked files; explicit tracked ignored files are excluded too.
    ignored, _ = _git(root, 'ls-files', '-z', '--cached', '--ignored', '--exclude-standard', deadline=deadline)
    ignored_paths = {os.fsdecode(v) for v in ignored.split(b'\0') if v}
    safe = []
    for path in paths:
        if path not in ignored_paths and _safe_relative(path, patterns):
            safe.append(path)
        elif requested is not None:
            # Report the user's reference, so a basename cannot reveal hidden locations.
            requested.excluded_references.update(requested.matching_references(path))
    paths = safe
    if requested is not None and any('/' not in name for name in requested.paths):
        listed_tracked, _ = _git(root, 'ls-files', '-z', '--cached', deadline=deadline)
        requested.prefer_tracked_basenames(paths, {os.fsdecode(value) for value in listed_tracked.split(b'\0') if value})
    limits = []
    if len(paths) > _MAX_PATHS:
        limits.append('Code structure display is limited to 300 safe paths; source inspection uses separate byte and time budgets.')
    paths = sorted(paths, key=_scheme_order)
    manifests = sorted((v for v in paths if _manifest(v)), key=_code_order)
    declarations = [v for v in paths if PurePosixPath(v).name == 'setup.py']
    docs = sorted((v for v in paths if _scheme_category(v) == 'docs'), key=_code_order)
    shared_stems = _shared_feature_stems(paths)
    code = sorted((v for v in paths if _scheme_category(v) == 'code' and v not in declarations), key=lambda v: (
        PurePosixPath(v).stem.casefold() not in _ENTRYPOINTS,
        PurePosixPath(v).stem.casefold() not in shared_stems, *_scheme_order(v)))
    production = [v for v in code if _production_path(v)]
    if production:
        code = production
    # Round-robin top-level areas so a backend does not hide a frontend.
    buckets = {}
    for value in code:
        buckets.setdefault(_area(value), deque()).append(value)
    chosen = []
    active = deque(buckets.values())
    while active:
        values = active.popleft()
        chosen.append(values.popleft())
        if values:
            active.append(values)
    # No category may consume the entire read budget before application code.
    # Preserve each category's order, with declared entrypoint overrides below.
    groups = deque(deque(group) for group in (manifests, declarations, docs, chosen) if group)
    candidates = []
    while groups:
        group = groups.popleft()
        candidates.append(group.popleft())
        if group:
            groups.append(group)
    if len(candidates) < len(paths):
        limits.append('Only selected source files and bounded declaration excerpts were inspected.')
    if inventory is not None:
        inventory.update(repository_paths_total=listed_count, excluded_paths=listed_count - len(paths),
                         safe_paths=len(paths), eligible_files=len(set(candidates)),
                         policy_omitted_paths=len(set(paths) - set(candidates)), listing_complete=True)
    return root, paths, candidates, limits


def _entrypoint_declarations(value, relative, *, preserve_order=False):
    """Read literal packaging declarations only; never import or evaluate setup code."""
    entries = []
    name = PurePosixPath(relative).name
    if name == 'pyproject.toml':
        project = tomllib.loads(value).get('project', {})
        for group in ('scripts', 'gui-scripts'):
            mapping = project.get(group, {})
            if isinstance(mapping, dict):
                entries.extend(list(mapping.items())[:_MAX_DEPENDENCIES])
    elif name == 'setup.py':
        try:
            parsed = ast.parse(value)
        except (SyntaxError, ValueError, RecursionError):
            return []
        setup_names, modules = set(), set()
        for node in parsed.body:
            if isinstance(node, ast.Import):
                modules.update(alias.asname or alias.name for alias in node.names if alias.name == 'setuptools')
            elif isinstance(node, ast.ImportFrom) and node.module == 'setuptools' and not node.level:
                setup_names.update(alias.asname or alias.name for alias in node.names if alias.name == 'setup')
        for statement in parsed.body:
            if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
                continue
            call = statement.value
            if _qualified_name(call.func) not in setup_names | {module + '.setup' for module in modules}:
                continue
            for keyword in call.keywords:
                if keyword.arg != 'entry_points' or not isinstance(keyword.value, ast.Dict):
                    continue
                for key, group in zip(keyword.value.keys, keyword.value.values):
                    if not isinstance(key, ast.Constant) or key.value not in ('console_scripts', 'gui_scripts'):
                        continue
                    if not isinstance(group, (ast.List, ast.Tuple)):
                        continue
                    for entry in group.elts[:_MAX_DEPENDENCIES]:
                        if isinstance(entry, ast.Constant) and isinstance(entry.value, str) and '=' in entry.value:
                            command, target = entry.value.split('=', 1)
                            entries.append((command.strip(), target.strip()))
    result = []
    for command, target in entries[:_MAX_DEPENDENCIES]:
        if (isinstance(command, str) and isinstance(target, str)
                and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}', command)
                and len(target) <= 240
                and re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', target, re.ASCII)):
            result.append((command, target))
    return list(dict.fromkeys(result)) if preserve_order else sorted(set(result))


def _entrypoint_targets(declarations, relative, available):
    targets = []
    for _, target in declarations:
        module = PurePosixPath(*target.split(':', 1)[0].split('.'))
        for base in (PurePosixPath(relative).parent, PurePosixPath(relative).parent / 'src'):
            for candidate in (str(base / module) + '.py', (base / module / '__init__.py').as_posix()):
                if candidate in available and candidate not in targets:
                    targets.append(candidate)
    return targets


def _guide_excerpt(value):
    """Retain original guide purpose, outline and short CLI-help descriptions."""
    from .scheme_passages import _passage_ranges

    ranges, _ = _passage_ranges(value)
    candidates = []
    prose_count = 0
    alternative_used = False
    for left, right in ranges:
        body = value[left:right]
        if re.match(r"[ \t]*(`{3,}|~{3,})", body):
            # CLI help is often one enormous fence. Its purpose prose remains
            # useful while usage flags/options tables are implementation detail.
            if re.search(r"(?m)^usage:[ \t]", body):
                purpose = re.search(
                    r"\n[ \t]*\n([\s\S]*?)\n[ \t]*\n(?:options|optional arguments|positional arguments):",
                    body)
                if purpose and len(purpose[1]) <= 600:
                    candidates.append((0, left + purpose.start(1), left + purpose.end(1)))
            continue
        headings = list(re.finditer(r"(?m)^#{1,6}[ \t]+[^\n]+", body))
        for heading in headings:
            candidates.append((2, left + heading.start(), left + heading.end()))
        # Prefer the introduction before later detailed configuration prose.
        alternative = not alternative_used and len(body) <= 700 and is_workflow_alternative(body)
        if (len(body) <= 600 or alternative) and (not headings or len(body.splitlines()) > len(headings)):
            if alternative:
                alternative_used = True
            candidates.append((0 if alternative else 1 if prose_count < 2 else 3, left, right))
            prose_count += 1
    selected = []
    for _, left, right in sorted(candidates):
        if any(left >= start and right <= end for start, end in selected):
            continue
        proposed = [(start, end) for start, end in selected if not (start >= left and end <= right)]
        proposed.append((left, right))
        proposed.sort()
        text = GAP.join(value[start:end] for start, end in proposed)
        if len(text) <= _MAX_EXCERPT and len(proposed) <= 24:
            selected = proposed
    if not selected:
        return select_passages(value, _MAX_EXCERPT)['text']
    return GAP.join(value[start:end] for start, end in selected)


def _documentation_excerpt(value):
    """Prefer a whole feature section over decorative README prefixes."""
    heading = re.search(r'(?im)^(#{1,6})[ \t]+(?:features?|capabilities)[ \t]*:?[ \t]*$', value)
    if heading is None:
        return (value if len(value) <= _MAX_EXCERPT else _guide_excerpt(value)), len(value) > _MAX_EXCERPT
    depth = len(heading[1])
    following = re.search(r'(?m)^#{1,' + str(depth) + r'}[ \t]+', value[heading.end():])
    end = heading.end() + following.start() if following else len(value)
    section = value[heading.start():end].rstrip()
    excerpt = select_passages(section, _MAX_EXCERPT)['text']
    # Keep short title/intro paragraphs when room remains; omit badges and logos.
    introduction = []
    for match in re.finditer(r'\S[\s\S]*?(?=\n[ \t]*\n|\Z)', value[:heading.start()]):
        paragraph = match[0]
        if re.match(r'(?:<|!\[|\[!\[)', paragraph):
            continue
        candidate = GAP.join([*introduction, paragraph, excerpt])
        if len(candidate) <= _MAX_EXCERPT:
            introduction.append(paragraph)
        if len(introduction) == 2:
            break
    result = GAP.join([*introduction, excerpt]) if introduction else excerpt
    return result, result != value


def _dependency(name, constraint, category, source_id, dependencies, limitations):
    if not isinstance(name, str) or not re.fullmatch(r'[@A-Za-z0-9_][@A-Za-z0-9._/-]{0,159}', name):
        limitations.add('Some malformed dependency declarations were omitted.')
        return
    if not isinstance(constraint, str) or len(constraint) > 300 or any(ord(c) < 32 for c in constraint):
        limitations.add('Some malformed dependency declarations were omitted.')
        return
    # Direct URLs, credentials, and local machine paths must not be echoed into diagrams.
    direct_reference = re.match(r'^\s*(?:\[[^\]\r\n]*\]\s*)?@', constraint)
    home_reference = re.match(r'^~(?![=])', constraint)
    if (any(token in constraint.lower() for token in ('://', 'git+', 'file:', ' @ ', '\\'))
            or direct_reference or home_reference or constraint.startswith('/')):
        constraint = 'Direct/local reference (location omitted)'
    identifier = 'dependency:' + _digest([name, category, source_id, constraint])[:20]
    dependencies.append({'id': identifier, 'name': name, 'constraint': constraint,
                         'category': category, 'source_ids': [source_id], 'purpose': ''})


def _pip_include(line):
    match = re.fullmatch(r'(?:-r\s*|--requirement(?:\s+|=))(.+)', line.split(' #', 1)[0])
    return match[1].strip().strip("\"'") if match else None


def _relative_requirement_path(path, include):
    # Match only against the safe repository path inventory before reading.
    return os.path.normpath(PurePosixPath(path).parent / include)


def _requirements(value, path, source_id, dependencies, includes, limitations):
    # Requirement file purpose is evidence; package names alone are not. Keep
    # documentation/CI/release declarations out of the runtime dependency group.
    parts = PurePosixPath(path.lower()).parts
    labels = set(re.split(r'[._-]+', PurePosixPath(path).stem.lower())) | set(parts[:-1])
    category = ('development:documentation' if labels & {'doc', 'docs', 'documentation'}
                else 'build' if labels & {'build', 'release', 'packaging'}
                else 'development' if labels & {'ci', 'dev', 'development', 'test', 'tests', 'testing'}
                else 'runtime')
    for line in re.sub(r'\\\r?\n', '', value).splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        requirement_include = _pip_include(line)
        if requirement_include:
            includes.append(_relative_requirement_path(path, requirement_include))
            continue
        if line.startswith('-'):
            limitations.add('Requirements options and constraints-file semantics are not evaluated.')
            continue
        requirement = re.split(r'\s+--hash(?:=|\s)', line.split(' #', 1)[0], maxsplit=1)[0].rstrip()
        match = _REQUIREMENT.fullmatch(requirement)
        if match:
            name, extras, constraint = match.groups()
            _dependency(name, (extras or '') + (constraint or '').strip(), category, source_id, dependencies, limitations)
        else:
            limitations.add('Some requirements declarations could not be parsed.')


def _manifest_excerpts(declared_excerpt, dependencies, source_id):
    """Keep complete normalized declarations with the exact chunk they cite."""
    chunks, lines, size = [], [], 0
    entries = [(line, None) for line in declared_excerpt.splitlines()]
    entries.extend((f"{item['name']} {item['constraint']} ({item['category']})", item)
                   for item in dependencies)
    for line, dependency in entries:
        if lines and size + len(line) + 1 > 3200:
            chunks.append('\n'.join(lines))
            lines, size = [], 0
        chunk_id = source_id if not chunks else f'{source_id}:part:{len(chunks) + 1}'
        if dependency is not None:
            dependency['source_ids'] = [chunk_id]
        lines.append(line)
        size += len(line) + (1 if len(lines) > 1 else 0)
    if lines:
        chunks.append('\n'.join(lines))
    return [(source_id if index == 0 else f'{source_id}:part:{index + 1}', value)
            for index, value in enumerate(chunks)]


def _parse_manifest(value, path, source_id, dependencies, includes, limitations, *, full=False):
    if is_environment_manifest(path):
        pip_requirements = []
        for name, constraint, category in environment_dependencies(value, limitations, pip_requirements):
            _dependency(name, constraint, category, source_id, dependencies, limitations)
        includes.extend(_relative_requirement_path(path, requirement)
                        for line in pip_requirements if (requirement := _pip_include(line)))
    elif PurePosixPath(path).name.lower() == 'package.json':
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError('Invalid package manifest')
        for field, category in (('dependencies', 'runtime'), ('devDependencies', 'development'),
                                ('optionalDependencies', 'optional'), ('peerDependencies', 'peer')):
            entries = parsed.get(field, {})
            if not isinstance(entries, dict):
                raise ValueError('Invalid dependency map')
            for name, constraint in entries.items():
                _dependency(name, constraint, category, source_id, dependencies, limitations)
    elif PurePosixPath(path).name.lower() == 'pyproject.toml':
        parsed = tomllib.loads(value)
        project = parsed.get('project', {})
        groups = [('runtime', project.get('dependencies', []))]
        if full:
            groups.append(('build', parsed.get('build-system', {}).get('requires', [])))
        for category, entries in project.get('optional-dependencies', {}).items():
            groups.append(('optional:' + category, entries))
        for category, entries in parsed.get('dependency-groups', {}).items():
            groups.append(('development:' + category, entries))
        for category, entries in groups:
            if not isinstance(entries, list):
                raise ValueError('Invalid Python dependency group')
            for entry in entries:
                match = _REQUIREMENT.fullmatch(entry) if isinstance(entry, str) else None
                if match:
                    name, extras, constraint = match.groups()
                    _dependency(name, (extras or '') + (constraint or '').strip(), category, source_id, dependencies, limitations)
                else:
                    limitations.add('Dynamic Python dependency groups are not evaluated.')
        if 'dependencies' in project.get('dynamic', []):
            dynamic = parsed.get('tool', {}).get('setuptools', {}).get('dynamic', {}).get('dependencies', {})
            files = dynamic.get('file', [])
            if isinstance(files, str):
                files = [files]
            if isinstance(files, list) and files and all(isinstance(file, str) for file in files):
                includes.extend(_relative_requirement_path(path, file) for file in files)
            else:
                limitations.add('Dynamic Python dependency declarations require a static manifest or import evidence.')
        if full:
            poetry = parsed.get('tool', {}).get('poetry', {})
            maps = [('runtime', poetry.get('dependencies', {})),
                    ('development', poetry.get('dev-dependencies', {}))]
            maps.extend(('development:' + group, data.get('dependencies', {}))
                        for group, data in poetry.get('group', {}).items())
            _dependency_maps(maps, source_id, dependencies, limitations)
        elif parsed.get('tool', {}).get('poetry'):
            limitations.add('Poetry dependency declarations are inventoried separately from graph evidence.')
    elif PurePosixPath(path).name.lower() == 'pipfile':
        parsed = tomllib.loads(value)
        _dependency_maps([('runtime', parsed.get('packages', {})),
                          ('development', parsed.get('dev-packages', {}))],
                         source_id, dependencies, limitations)
    else:
        _requirements(value, path, source_id, dependencies, includes, limitations)


def _dependency_maps(maps, source_id, dependencies, limitations):
    for category, entries in maps:
        for name, versions in entries.items():
            for spec in versions if isinstance(versions, list) else [versions]:
                if isinstance(spec, dict):
                    spec = ('Direct/local reference (location omitted)' if set(spec) & {'url', 'git', 'path', 'file'}
                            else spec.get('version', ''))
                _dependency(name, spec, category, source_id, dependencies, limitations)


def _dependency_name(name):
    return re.sub(r'[-_.]+', '-', name.casefold())


def _dependency_inventory(root_fd, paths, repository_linked):
    """Inspect all safe manifest and Python paths separately from model excerpts."""
    limitations = set()
    if root_fd is None:
        limitations.add('The linked repository could not be inspected.' if repository_linked else 'No repository is linked.')
        return {'dependencies': [], 'sources': [], 'coverage': {
            'files_total': None if repository_linked else 0, 'files_scanned': 0,
            'limitations': sorted(limitations)}}, set(), None

    available = set(paths)
    setup_files = {path for path in paths if PurePosixPath(path).name.lower() in {'setup.py', 'setup.cfg'}}
    manifests = {path for path in paths if _manifest(path)} | setup_files
    python_files = {path for path in paths if PurePosixPath(path).suffix.lower() == '.py'}
    pending = deque(sorted(manifests, key=_scheme_order) + sorted(python_files - manifests, key=_scheme_order))
    queued = manifests | python_files
    local_modules = local_python_modules(paths)
    dependencies, sources, imports = [], [], {}
    scanned = 0
    stopped = None
    budget = _InspectionBudget(_MAX_DEPENDENCY_READ_BYTES, _DEPENDENCY_INSPECTION_SECONDS)

    def add_evidence(relative, items, provenance):
        items = list({item['id']: item for item in items}.values())
        if not items:
            return
        source_id = 'dependency-evidence:' + hashlib.sha256(f'{provenance}:{relative}'.encode()).hexdigest()[:24]
        for chunk_id, excerpt in _manifest_excerpts('', items, source_id):
            sources.append(_source(chunk_id, relative, excerpt, provenance, path=relative))
        dependencies.extend(items)

    while pending:
        relative = pending.popleft()
        canonical_id = 'repository:file:' + hashlib.sha256(relative.encode()).hexdigest()[:24]
        declarations, includes = [], []
        try:
            value = _read(root_fd, relative, budget=budget, max_file_bytes=_MAX_DEPENDENCY_FILE_BYTES,
                          python_encoding=relative in python_files)
            if relative in setup_files:
                for name, constraint, category in setup_dependencies(value, relative, limitations):
                    _dependency(name, constraint, category, canonical_id, declarations, limitations)
            elif relative in manifests:
                _parse_manifest(value, relative, canonical_id, declarations, includes, limitations, full=True)
            if relative in python_files:
                for name, constraint, category in python_imports(value, relative, local_modules, limitations):
                    normalized = _dependency_name(name)
                    if normalized not in imports:
                        imports[normalized] = relative, canonical_id, name, constraint, category
            add_evidence(relative, declarations, 'dependency manifest')
            for include in includes:
                if include not in available:
                    limitations.add('Some pip requirements include files were unavailable or outside the safe repository paths.')
                elif include not in queued:
                    queued.add(include)
                    manifests.add(include)
                    pending.appendleft(include)
            scanned += 1
        except _InspectionStopped as exc:
            stopped = exc.reason
            limitations.add(f'The dependency scan reached its {stopped.replace("_", " ")} before checking every file.')
            break
        except (OSError, ValueError, TypeError, AttributeError, RecursionError, SyntaxError, LookupError):
            limitations.add('Some dependency inputs were unreadable, malformed, or exceeded the 2 MiB per-file limit.')

    declared = {_dependency_name(item['name']) for item in dependencies}
    import_files = {}
    for normalized in imports.keys() - declared:
        relative, canonical_id, name, constraint, category = imports[normalized]
        _dependency(name, constraint, category, canonical_id, import_files.setdefault(relative, []), limitations)
    for relative, items in sorted(import_files.items()):
        add_evidence(relative, sorted(items, key=lambda item: item['name'].casefold()), 'Python import')
    dependencies.sort(key=lambda item: (item['category'].startswith('import:'), item['name'].casefold()))
    return {'dependencies': dependencies, 'sources': sources, 'coverage': {
        'files_total': len(queued), 'files_scanned': scanned, 'limitations': sorted(limitations),
    }}, set(imports), stopped


def dependency_repository_identity(project_id, root, directory_stat):
    if not stat.S_ISDIR(directory_stat.st_mode):
        return None
    return _digest(['scheme-dependency-repository-v1', project_id, str(root),
                    directory_stat.st_dev, directory_stat.st_ino])


def _qualified_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return parent + '.' + node.attr if parent else node.attr
    if isinstance(node, ast.Call):
        name = _qualified_name(node.func)
        return name + '(...)' if name else ''
    return ''


def _mask_js(value):
    """Mask literals/comments for conservative lexical JS inspection, not a parser."""
    chars = list(value)
    index, length = 0, len(chars)
    while index < length:
        begin = index
        if value.startswith('//', index):
            index = value.find('\n', index)
            if index < 0:
                index = length
        elif value.startswith('/*', index):
            end = value.find('*/', index + 2)
            index = length if end < 0 else end + 2
        elif value[index] in ('"', "'", '`'):
            quote = value[index]
            index += 1
            while index < length:
                if value[index] == '\\':
                    index += 2
                elif value[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
            index = min(index, length)
        else:
            index += 1
            continue
        for offset in range(begin, index):
            if chars[offset] != '\n':
                chars[offset] = ' '
    return ''.join(chars)


_ROUTINE_CALLS = {'isinstance', 'len', 'set', 'list', 'dict', 'tuple', 'str', 'int', 'bool', 'any', 'all',
                  'min', 'max', 'round', 'sorted', 'enumerate', 'range', 'getattr', 'hasattr', 'ord',
                  'ValueError', 'TypeError', 'RuntimeError', 'Error', 'setTimeout', 'clearTimeout'}


def _call_summary(names, preferred=(), *, focus=None):
    def priority(name):
        leaf = name.rsplit('.', 1)[-1]
        return (-(focus(name) if focus else 0),
                name in _ROUTINE_CALLS or leaf in {'append', 'extend', 'strip', 'get', 'items', 'values', 'keys', 'join'},
                name not in preferred, leaf.startswith(('use', 'set')), name.startswith('_'))
    return ', '.join(sorted(names, key=priority)[:12])


_COMPARISONS = {
    ast.Eq: '==', ast.NotEq: '!=', ast.Lt: '<', ast.LtE: '<=', ast.Gt: '>', ast.GtE: '>=',
    ast.Is: 'is', ast.IsNot: 'is not', ast.In: 'in', ast.NotIn: 'not in',
}


def _predicate_shape(node, depth=0):
    """Describe control flow without including argument or configuration values."""
    if depth >= 6:
        return '<expression>'
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f'{_predicate_shape(node.value, depth + 1)}.{node.attr}'
    if isinstance(node, ast.Subscript):
        return f'{_predicate_shape(node.value, depth + 1)}[{_predicate_shape(node.slice, depth + 1)}]'
    if isinstance(node, ast.Constant):
        return repr(node.value) if node.value is None or type(node.value) is bool else '<literal>'
    if isinstance(node, ast.Call):
        return f'{_predicate_shape(node.func, depth + 1)}(...)'
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return f'not ({_predicate_shape(node.operand, depth + 1)})'
    if isinstance(node, ast.BoolOp):
        operator = ' and ' if isinstance(node.op, ast.And) else ' or '
        operands = operator.join(_predicate_shape(value, depth + 1) for value in node.values[:8])
        return f'({operands}{" …" if len(node.values) > 8 else ""})'
    if isinstance(node, ast.Compare):
        if (isinstance(node.left, ast.Name) and node.left.id == '__name__'
                and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Eq, ast.NotEq))
                and isinstance(node.comparators[0], ast.Constant) and node.comparators[0].value == '__main__'):
            return f'__name__ {_COMPARISONS[type(node.ops[0])]} "__main__"'
        comparison = [_predicate_shape(node.left, depth + 1)]
        for operator, operand in zip(node.ops[:8], node.comparators[:8]):
            comparison.extend((_COMPARISONS[type(operator)], _predicate_shape(operand, depth + 1)))
        if len(node.ops) > 8:
            comparison.append('…')
        return ' '.join(comparison)
    return '<expression>'


def _client_navigation_declarations(value, masked, owner_at):
    declarations = []
    for match in _JS_HASH_ROUTE.finditer(value):
        if (not masked[match.start():match.start('quote')].strip()
                or not (value.startswith(match['quote'], match.end()) or value.startswith('${', match.end()))):
            continue
        owner = owner_at(match.start())
        operation = 'returns' if match['operation'].startswith('return') else 'checks'
        subject = f'{owner} {operation}' if owner and owner != 'module' else operation
        declarations.append((value.count('\n', 0, match.start()) + 1, f'Client navigation {subject} {match["fragment"]}'))
    return declarations


def _parse_python_excerpt(value, *, truncated):
    try:
        return ast.parse(value)
    except (ValueError, RecursionError):
        return None
    except SyntaxError as error:
        if not truncated:
            return None
        line = error.lineno
    # A byte boundary can leave an unfinished expression or block at the end.
    # Recover only the nearby, parseable prefix and keep all original line numbers.
    lines = value.split('\n')
    floor = max(1, len(lines) - 40)
    for _ in range(4):
        if line is None or not floor < line <= len(lines) + 1:
            return None
        lines = lines[:line - 1]
        try:
            return ast.parse('\n'.join(lines))
        except (ValueError, RecursionError):
            return None
        except SyntaxError as error:
            line = min(error.lineno, len(lines)) if error.lineno is not None else None
    return None


def _structural_excerpt(value, relative, *, focus=None, entrypoints=(), trace_imports=None, route_imports=None,
                        named_python=False, admitted_handoffs=None, route_support=None, truncated=False):
    """Bounded declarations, call syntax and selected structural details."""
    imports, declarations, calls, migration_sql, routes = [], [], [], [], []
    flow, workflow, choices, handoffs, route_owners = [], [], [], [], set()
    lifecycle, artifacts = [], []
    relationships = {}
    if PurePosixPath(relative).suffix.lower() == '.py':
        parsed = _parse_python_excerpt(value, truncated=truncated)
        if parsed is not None:
            if _python_migration(relative):
                migration_sql = compact_migration_sql(migration_sql_declarations(parsed))
            elif focus:
                handoffs = local_handoff_rows(parsed, focus, shape=_predicate_shape, structured=relationships)
                routes = python_route_declarations(parsed)
                flow = python_flow_declarations(parsed, focus, predicate_shape=_predicate_shape)
                if named_python:
                    traced, _ = python_entrypoint_trace(parsed, entrypoints, predicate_shape=_predicate_shape,
                                                       distinguish_fixed_inputs=True)
                    if traced:
                        downstream = traced[4:]
                        scoped = sorted(downstream, key=lambda row: -focus(row[1]))
                        handoff_lines = {line for line, _ in handoffs[:2]}
                        paired = [row for row in downstream if row[0] in handoff_lines]
                        ordered = dict.fromkeys([*traced[:4], *paired,
                                                 *(row for pair in zip(downstream, scoped) for row in pair)])
                        flow = [(line, label.replace('Python entrypoint flow ', 'Entry flow ', 1))
                                for line, label in ordered]
                    guarded_options = {name for _, row in flow if ' under ' in row
                                       for name in re.findall(r'\bargs\.([A-Za-z_]\w*)', row.partition(':')[0])}
                    choices = sorted((row for row in cli_choice_rows(parsed)
                                      if focus(row[1]) or row[1].split()[1][2:].replace('-', '_') in guarded_options),
                                     key=lambda row: -focus(row[1]))
            elif trace_imports is not None:
                flow, called = python_entrypoint_trace(parsed, entrypoints, predicate_shape=_predicate_shape,
                                                       lifecycle_rows=lifecycle, callback_symbols=route_imports,
                                                       artifact_rows=artifacts)
                trace_imports.update(called)
                workflow = workflow_sql_rows(parsed)
                candidates = [row for row in python_route_declarations(parsed)
                              if row[1].startswith('HTTP route ') and _WORKFLOW_ROUTE.search(row[1])]
                if candidates:
                    # Give POST and GET handlers room before auxiliary endpoints.
                    candidates.sort(key=lambda row: (
                        bool(re.search(r'/(?:preview|status|summary|cancel) ', row[1])),
                        row[1].split(' -> ')[0].count('/'), row[0]))
                    routes = candidates[:5]
                    handler_names = Counter(node.name for node in ast.walk(parsed)
                                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
                    route_owners = {name for _, row in routes if handler_names[name := row.split(' -> ')[-1]] == 1}
                    if route_imports is not None:
                        imported = python_imported_symbols(parsed)
                        for handler in ast.walk(parsed):
                            if isinstance(handler, (ast.FunctionDef, ast.AsyncFunctionDef)) and handler.name in route_owners:
                                calls = python_route_return_calls(handler)
                                if route_support is not None:
                                    route_support.update(calls)
                                route_imports.update((name, imported[name]) for name in calls if name in imported)

            class Visitor(ast.NodeVisitor):
                def __init__(self):
                    self.scope = []
                    self.branches = []
                    self.conditions = []
                    self.observed_calls = {}

                def owner(self):
                    return '.'.join(self.scope) or 'module level'

                def visit_Import(self, node):
                    imports.append((node.lineno, 'import ' + ', '.join(alias.name for alias in node.names)))

                def visit_ImportFrom(self, node):
                    imports.append((node.lineno, 'from ' + '.' * node.level + (node.module or '')
                                    + ' import ' + ', '.join(alias.name for alias in node.names)))

                def visit_FunctionDef(self, node):
                    declarations.append((node.lineno, 'def ' + node.name + '(...)'))
                    self.scope.append(node.name)
                    outer_branches, self.branches = self.branches, []
                    # Defaults/decorators are not function-body runtime evidence.
                    for child in node.body:
                        self.visit(child)
                    self.branches = outer_branches
                    self.scope.pop()

                visit_AsyncFunctionDef = visit_FunctionDef

                def visit_ClassDef(self, node):
                    declarations.append((node.lineno, 'class ' + node.name))
                    self.scope.append(node.name)
                    for child in node.body:
                        self.visit(child)
                    self.scope.pop()

                def visit_If(self, node):
                    if not focus:
                        self.generic_visit(node)
                        return
                    predicate = _predicate_shape(node.test)
                    self.conditions.append((node.lineno, f'{self.owner()} condition: if {predicate}'))
                    self.visit(node.test)
                    for condition, statements in ((f'if {predicate}', node.body),
                                                  (f'else of if {predicate}', node.orelse)):
                        self.branches.append((node.lineno, condition))
                        for child in statements:
                            self.visit(child)
                        self.branches.pop()

                def visit_Lambda(self, node):
                    if not focus:
                        self.generic_visit(node)

                def visit_Call(self, node):
                    name = _qualified_name(node.func)
                    if name and (self.scope or focus):
                        key = self.owner(), tuple(self.branches)
                        names = self.observed_calls.setdefault(key, [node.lineno, []])[1]
                        if name not in names:
                            if len(names) < 64:
                                names.append(name)
                            elif focus and focus(name):
                                least = min(range(len(names)), key=lambda index: focus(names[index]))
                                if focus(names[least]) < focus(name):
                                    names[least] = name
                    self.generic_visit(node)

            preferred = set()
            for node in ast.walk(parsed):
                if isinstance(node, ast.ImportFrom):
                    preferred.update(alias.asname or alias.name for alias in node.names
                                     if (alias.asname or alias.name)[:1].islower())
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Return)) and isinstance(node.value, ast.Call):
                    preferred.add(_qualified_name(node.value.func))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {'commit', 'flush', 'rollback'}:
                    preferred.add(_qualified_name(node.func))
            visitor = Visitor()
            visitor.visit(parsed)
            calls = visitor.conditions
            for (scope, branches), (line, names) in visitor.observed_calls.items():
                context = ' within ' + '; '.join(condition for _, condition in branches) if branches else ''
                calls.append((line, scope + context + ' uses call syntax: ' + _call_summary(names, preferred, focus=focus)))
    if not declarations and not imports and not calls:
        masked = _mask_js(value)
        declared_positions = set()
        declaration_pattern = re.compile(r'\b(?:export\s+(?:default\s+)?)?(?:(?:async\s+)?(?:function|def)\s+|class\s+|(?:const|let|var|interface|type|enum)\s+)([A-Za-z_$][\w$]*)')
        for match in declaration_pattern.finditer(masked):
            number = value.count('\n', 0, match.start()) + 1
            declarations.append((number, 'declaration ' + match[1]))
            declared_positions.add(match.start(1))
        import_pattern = re.compile(r"(?:\bfrom\s*|\bimport\s*(?:\(\s*)?)['\"]([^'\"]{1,180})['\"]")
        for match in import_pattern.finditer(value):
            if not masked[match.start():match.start() + 4].strip():
                continue
            target = match[1]
            if '://' in target or any(char in target for char in ('?', '#')):
                target = '[external module location omitted]'
            imports.append((value.count('\n', 0, match.start()) + 1, 'import ' + target))
        seen_calls = set()
        owner_at = focused_call_owner(masked) if focus else None
        if owner_at and PurePosixPath(relative).suffix.lower() in _JS_EXTENSIONS:
            routes = _client_navigation_declarations(value, masked, owner_at)
            flow = sorted(focused_ui_declarations(value, masked, owner_at), key=lambda item: -focus(item[1]))
        function_marks = [] if focus else [(match.start(1), match[1]) for match in re.finditer(
            r'\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(', masked)]
        grouped_calls = {}
        for match in re.finditer(r'\b([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)(?:<[^<>;{}\n]{1,120}>)?\s*\(', masked):
            name = re.sub(r'\s+', '', match[1])
            if ((not focus and name in seen_calls)
                    or name in {'if', 'for', 'while', 'switch', 'catch', 'function', 'import', 'super', 'return', 'async'}
                    or match.start(1) in declared_positions):
                continue
            scope = (owner_at(match.start(1)) if owner_at else
                     next((name for start, name in reversed(function_marks) if start < match.start()), 'module'))
            if scope is None:
                continue
            key = (scope, name) if focus else name
            if key in seen_calls:
                continue
            seen_calls.add(key)
            number = value.count('\n', 0, match.start()) + 1
            group = grouped_calls.setdefault(scope, [number, []])
            if len(group[1]) < 64 or focus and focus(name):
                group[1].append(name)
        calls = [(number, scope + ' lexical call syntax: ' + _call_summary(names, focus=focus))
                 for scope, (number, names) in grouped_calls.items()]
    # Public declarations and their call groups should not disappear behind long
    # import blocks or private helpers. Interleave groups within the same hard cap.
    declarations.sort(key=lambda item: (item[1].startswith('def _'), item[0]))
    calls.sort(key=lambda item: (item[1].startswith('_'), item[0]))
    buckets = [migration_sql, declarations, calls, imports]
    if focus:
        for bucket in buckets:
            bucket.sort(key=lambda item: -focus(item[1]))
    rendered = ['Static syntax only; call names do not prove runtime execution.']
    used = len(rendered[0]) + 1
    # Keep room for explicit routes and concrete handoffs ahead of call-name lists.
    ordered_routes = sorted(routes, key=lambda item: -focus(item[1])) if focus else routes
    # Related lines form one budget unit. Keep a short omission record if the
    # entire unit does not fit alongside the other structural evidence.
    handoff_budget = min(380, _MAX_EXCERPT - used)
    selected_handoffs = handoffs[:2]
    omissions = [f'L{number}: {description.partition(":")[0]}. Handoff details omitted by excerpt budget.'
                 for number, description in selected_handoffs]
    for index, (number, description) in enumerate(selected_handoffs):
        line = f'L{number}: {description}'
        reserved = sum(len(omitted) + 1 for omitted in omissions[index + 1:])
        if len(line) + 1 + reserved > handoff_budget:
            line = omissions[index]
        if len(line) + 1 <= handoff_budget:
            rendered.append(line)
            if admitted_handoffs is not None and line != omissions[index]:
                admitted_handoffs.extend(relationships.get((number, description), ()))
            used += len(line) + 1
            handoff_budget -= len(line) + 1
    full_trace = not focus or (named_python and flow and flow[0][1].startswith('Entry flow '))
    flow_allowance = 1400 if full_trace else _MAX_FOCUSED_FLOW_CHARS
    route_calls = [row for row in calls if row[1].split(' uses call syntax:')[0].rsplit('.', 1)[-1] in route_owners]
    artifact_calls = [row for row in dict.fromkeys(artifacts) if row[1].startswith('Workflow artifact call ')]
    callback_calls = sorted((row for row in set(artifacts) if row[1].startswith('Workflow callback ')), reverse=True)
    for rows, allowance, count in ((lifecycle, 360, 4),
                                    ([*artifact_calls, *callback_calls], 320, 2),
                                    (workflow, 480, 8),
                                    (ordered_routes, _MAX_FOCUSED_ROUTE_CHARS, 8),
                                    (route_calls, 700, 5),
                                    (choices, 200, 2),
                                    (flow, flow_allowance, 16 if full_trace else 8)):
        ceiling = min(used + allowance, _MAX_EXCERPT)
        for number, statement in rows[:count]:
            line = f'L{number}: {statement[:320]}'
            if used + len(line) + 1 <= ceiling:
                rendered.append(line)
                used += len(line) + 1
    positions = [0] * len(buckets)
    while any(positions[i] < len(bucket) for i, bucket in enumerate(buckets)):
        for i, bucket in enumerate(buckets):
            if positions[i] >= len(bucket):
                continue
            number, statement = bucket[positions[i]]
            positions[i] += 1
            line = f'L{number}: {statement[:320]}'
            if used + len(line) + 1 <= _MAX_EXCERPT:
                rendered.append(line)
                used += len(line) + 1
    return '\n'.join(rendered) if len(rendered) > 1 else ''


def _focused_record_windows(db, query, table, columns, params):
    """Locate an FTS fragment in the original text and read one bounded window."""
    return db.execute(text(
        f'WITH matches AS MATERIALIZED ({query}), '
        'fragments AS (SELECT id,score,replace(fragment,:gap,\'\') AS fragment FROM matches), '
        'anchors AS MATERIALIZED (SELECT fragments.id,score,'
        'instr(fragment,:start) AS first_match,instr(fragment,:end) AS first_end, '
        'instr(record.content,replace(replace(fragment,:start,\'\'),:end,\'\')) AS fragment_start, '
        f'length(record.content) AS content_length FROM fragments JOIN {table} record '
        'ON record.id=fragments.id AND record.project_id=:pid), '
        'positions AS (SELECT *, CASE WHEN first_match>0 AND fragment_start>0 '
        'AND first_end>first_match+length(:start) THEN fragment_start+first_match-2 END AS match_start '
        'FROM anchors), '
        'windows AS (SELECT *, max(0,min(content_length-:read_chars,'
        'coalesce(match_start,0)-:read_chars/2)) AS window_start FROM positions) '
        f'SELECT windows.id,{columns},substr(record.content,window_start+1,:read_chars) AS window, '
        'window_start,content_length,match_start,first_end-first_match-length(:start) AS match_length '
        f'FROM windows JOIN {table} record '
        'ON record.id=windows.id AND record.project_id=:pid ORDER BY score,windows.id DESC'
    ), params).mappings().all()


def _records(db, project, *, scope=None):
    from .history_duplicates import deduplicate_sources
    sources, markers, limitations = [], [], []
    if len(project.description) > 1600 or len(project.goals) > 1600:
        limitations.append('Project description or goals were truncated to 1,600 characters each.')
    if len(project.architecture_mermaid) > 2400:
        limitations.append('Saved architecture was truncated to 2,400 characters.')
    objective = '\n'.join(v for v in (project.name, project.description[:1600], project.goals[:1600]) if v)
    sources.append(_source('project:objective', 'Project description and goals', objective,
                           'project metadata'))
    if project.architecture_mermaid:
        sources.append(_source('project:architecture', 'Saved architecture description',
                               project.architecture_mermaid[:2400], 'user-maintained architecture'))
    sessions = db.execute(text('SELECT id,thread_id,history_indexed_at,history_indexed_activity,codex_activity_at '
        'FROM codex_sessions WHERE project_id=:pid AND is_linked=1 ORDER BY id LIMIT 33'), {'pid': project.id}).mappings().all()
    markers.extend(dict(row) for row in sessions)
    if len(sessions) > 32:
        limitations.append('Freshness checks include only the first 33 linked session metadata records.')
    if sessions:
        limitations.append('Conversation coverage uses at most 12 relevant or recent stored excerpts; other discussions may be omitted.'
                           if scope else 'Conversation coverage uses at most 12 recent stored excerpts; earlier discussion may be omitted.')
    if any(not row['history_indexed_at'] or not row['history_indexed_activity']
           or not row['codex_activity_at'] or row['history_indexed_activity'] != row['codex_activity_at']
           for row in sessions):
        limitations.append('Some linked conversations have no current saved index; no live Codex history was read.')
    turns = db.execute(text('SELECT h.id,h.session_id,h.turn_id,substr(h.content,1,:read_chars) AS window,'
        '0 AS window_start,length(h.content) AS content_length '
        'FROM codex_history_turns h JOIN codex_sessions s ON s.id=h.session_id AND s.project_id=h.project_id '
        "WHERE h.project_id=:pid AND s.is_linked=1 AND h.status!='inProgress' ORDER BY h.id DESC LIMIT 12"),
        {'pid': project.id, 'read_chars': READ_CHARS}).mappings().all()
    imported = f"kind='document' AND ({CONVERSATION_NOTE_SQL} OR lower(reference) GLOB :share) AS share"
    notes = db.execute(text('SELECT id,substr(title,1,160) AS title,substr(content,1,:read_chars) AS window,'
                           f'0 AS window_start,kind,updated_at,created_at,{imported},'
                           'length(content) AS content_length,length(title) AS title_length '
                           'FROM notes WHERE project_id=:pid ORDER BY id DESC LIMIT 7'),
                       {'pid': project.id, 'read_chars': READ_CHARS, 'share': _SHARE_REFERENCE}).mappings().all()
    if scope and scope.fts_query:
        from .history_search import search_markers

        start, end = search_markers()
        params = {'pid': project.id, 'read_chars': READ_CHARS, 'query': scope.fts_query, 'share': _SHARE_REFERENCE,
                  'start': start, 'end': end, 'gap': start + end}
        focused_turns = _focused_record_windows(db,
            'SELECT h.id,substr(snippet(codex_history_fts,0,:start,:end,:gap,24),1,:read_chars) AS fragment,'
            'codex_history_fts.rank AS score '
            'FROM codex_history_fts JOIN codex_history_turns h ON h.id=codex_history_fts.rowid '
            'JOIN codex_sessions s ON s.id=h.session_id AND s.project_id=h.project_id '
            "WHERE codex_history_fts MATCH :query AND h.project_id=:pid AND s.is_linked=1 AND h.status!='inProgress' "
            'ORDER BY score,h.id DESC LIMIT 12', 'codex_history_turns', 'record.session_id,record.turn_id', params)
        focused_notes = _focused_record_windows(db,
            'SELECT n.id,substr(snippet(search_index,4,:start,:end,:gap,24),1,:read_chars) AS fragment,'
            'search_index.rank AS score '
            'FROM search_index JOIN notes n ON n.id=search_index.entity_id AND n.project_id=search_index.project_id '
            "WHERE search_index MATCH :query AND search_index.entity_type='notes' AND n.project_id=:pid "
            'ORDER BY score,n.id DESC LIMIT 7', 'notes',
            f'substr(record.title,1,160) AS title,record.kind,record.updated_at,record.created_at,{imported},'
            'length(record.title) AS title_length',
            params)
        # Keep the best window when a matching record also appears in the recent
        # results. Relevance order takes precedence over the recency fallback.
        focused_turn_ids = {row['id'] for row in focused_turns}
        focused_note_ids = {row['id'] for row in focused_notes}
        turns = [*focused_turns, *(row for row in turns if row['id'] not in focused_turn_ids)][:12]
        notes = [*focused_notes, *(row for row in notes if row['id'] not in focused_note_ids)][:7]
    # Bound discussion context independently so larger notes cannot crowd out all
    # repository evidence from the existing 40,000-character source package.
    turn_budget = 12_000 if notes else 24_000
    note_budget = 12_000 if turns else 24_000

    def passage(value, original_length, remaining, window_start=0, match_start=None, match_length=0):
        begin, end = 0, len(value)
        if original_length > len(value):
            # Cut partial paragraphs at either edge of a bounded read. Quoted
            # spans still address the original source, before any trimming.
            boundaries = list(re.finditer(r'\n[ \t]*\n', value))
            if window_start:
                begin = boundaries[0].end() if boundaries else end
            if window_start + len(value) < original_length:
                end = boundaries[-1].end() if boundaries else 0
        limit = min(EXCERPT_CHARS, max(0, remaining))
        selected = select_passages(value[begin:end], limit, priority=scope.score if scope else None)
        local_match = match_start - window_start if match_start is not None else -1
        match_end = local_match + match_length
        excerpt_offset = begin
        if (scope and 0 <= local_match < match_end <= len(value) and match_length <= limit
                and not any(begin + part['start'] <= local_match and match_end <= begin + part['end']
                            for part in selected['spans'])):
            # A verified body match can lie in a paragraph that is larger than
            # the excerpt budget or cut off by the read window. Keep the exact
            # source text around it without assigning a speaker to the slice.
            width = min(len(value), limit, max(1200, match_length))
            center = (local_match + match_end) // 2
            left = min(len(value) - width, max(0, center - width // 2))
            right = left + width
            excerpt = value[left:right]
            if scope.score(excerpt) > scope.score(selected['text']):
                selected = {'text': excerpt, 'spans': [{'start': left, 'end': right}], 'omitted': False}
                excerpt_offset = 0
                limitations.append('Some discussion excerpts contain only the text around a focused match.')
        if window_start:
            # A window can start inside a message. Its local role-parser
            # ordinal is not an ordinal in the full saved conversation.
            offset = window_start + excerpt_offset
            selected['spans'] = [{'start': part['start'] + offset, 'end': part['end'] + offset}
                                 for part in selected['spans'] if part['end'] > part['start']]
        if selected['omitted']:
            limitations.append('Some discussion sections were omitted from bounded whole-passage selection; omitted text is not evidence of absence.')
        if original_length > len(value):
            limitations.append(
                'Some long discussions were inspected in bounded windows; material outside those windows may be omitted.'
                if scope else 'Some discussions exceeded the 65,536-character inspection bound; later content was not inspected.')
        return selected, {'excerpt_spans': selected['spans'],
                          'inspected_hash': hashlib.sha256(value.encode()).hexdigest(),
                          'inspected_chars': len(value), 'original_chars': original_length,
                          **({'inspected_start': window_start} if window_start else {})}

    turn_sources = [_source(f"session:{row['session_id']}:turn:{row['turn_id']}",
        f"Session #{row['session_id']}, turn {row['turn_id']}", row['window'], 'Codex conversation',
        session_id=row['session_id'], turn_id=row['turn_id'], original_chars=row['content_length'],
        **({'inspected_start': row['window_start']} if row['window_start'] else {}),
        **({'match_start': row['match_start'], 'match_length': row['match_length']}
           if row.get('match_start') is not None else {}))
        for row in turns]
    # Confirm shared-prefix identity before charging the context budget; repeated
    # forks must not crowd out complementary turns that follow them.
    turn_sources = deduplicate_sources(db, project.id, turn_sources)
    excerpt_count = 0
    for source in turn_sources:
        selected, metadata = passage(source['text'], source['original_chars'], turn_budget,
                                     source.get('inspected_start', 0),
                                     source.pop('match_start', None), source.pop('match_length', 0))
        if selected['text']:
            sources.append({**source, **metadata, 'text': selected['text'],
                            'content_hash': hashlib.sha256(selected['text'].encode()).hexdigest()})
            turn_budget -= len(selected['text'])
            excerpt_count += 1
    if len(notes) > 6:
        limitations.append('At most six relevant or recent notes or imported documents were included.'
                           if scope else 'Only the six most recent notes or imported documents were included.')
    if any(row['title_length'] > 160 for row in notes[:6]):
        limitations.append('Some note titles were truncated to 160 characters.')
    for row in notes[:6]:
        selected, metadata = passage(row['window'], row['content_length'], note_budget, row['window_start'],
                                     row.get('match_start'), row.get('match_length', 0))
        if selected['text']:
            sources.append(_source(f"note:{row['id']}", row['title'], selected['text'],
                'imported discussion' if row['kind'] == 'document' else 'project note',
                date=row['updated_at'], **metadata,
                **({'note_id': row['id'], 'note_created_at': row['created_at'], 'share': True} if row['share'] else {})))
            note_budget -= len(selected['text'])
    brief = db.execute(text('SELECT substr(sections,1,1800),generated_at,length(sections) FROM project_briefs WHERE project_id=:pid'),
                       {'pid': project.id}).first()
    if brief:
        if brief[2] > 1800:
            limitations.append('The previous AI summary was truncated to a 1,800-character excerpt.')
        sources.append(_source('brief:previous', 'Previous AI project summary (interpretation)', brief[0],
                               'prior AI interpretation', date=brief[1]))
    return sources, markers, limitations, excerpt_count


def collect_evidence(engine, project_id, limits=None, *, scope=None, persist_cache=True):
    """Inspect only bounded local inputs. The resulting snapshot preserves supplied evidence."""
    started = time.monotonic()
    focus = _EvidenceScope(scope) if scope and scope.strip() else None
    source_budget, read_budget, package_budget = scheme_inspection_limits() if limits is None else limits
    limitations = set()
    if focus:
        limitations.add('Focused inspection prioritizes scope terms within the existing evidence limits. Relevant files or discussions may be omitted.')
    freshness_unknown = False
    with Session(engine) as db:
        project = require_project(db, project_id)
        repository_path = project.repository_path
        objective = project.name
        try:
            records, session_markers, record_limits, excerpt_count = (
                _records(db, project, scope=focus) if focus else _records(db, project))
        except SQLAlchemyError:
            db.rollback()
            records = [_source('project:objective', 'Project name', objective, 'project metadata')]
            session_markers, excerpt_count = [], 0
            record_limits = ['Stored conversation or project knowledge evidence could not be read.']
            freshness_unknown = True
        try:
            knowledge = collect_published_knowledge(db, project_id)
        except SQLAlchemyError:
            db.rollback()
            knowledge = {'sources': [], 'metadata': {'status': 'unavailable', 'available_items': 0,
                'included_items': 0, 'omitted_items': 0}, 'marker': {'status': 'unavailable'},
                'limitations': ['Published Knowledge could not be read; repository and conversation evidence remain available.']}
            freshness_unknown = True
        records = records[:1] + knowledge['sources'] + records[1:]
        limitations.update(knowledge['limitations'])
        freshness_unknown |= knowledge['metadata'].get('status') == 'unavailable'
    freshness_unknown |= any('no current saved index' in value for value in record_limits)
    limitations.update(record_limits)
    root = None
    paths, candidates, repo_limits = [], [], []
    inventory = dict.fromkeys(('repository_paths_total', 'excluded_paths', 'safe_paths',
                               'eligible_files', 'policy_omitted_paths'))
    inventory['listing_complete'] = False
    policy = {'max_read_bytes': read_budget, 'max_inspection_seconds': _INSPECTION_SECONDS,
              'max_file_bytes': _MAX_BYTES, 'max_source_chars': source_budget,
              'max_package_chars': package_budget, 'max_tree_paths': _MAX_PATHS,
              'max_metadata_bytes': _MAX_METADATA_BYTES, 'max_metadata_seconds': _METADATA_SECONDS}
    policy['max_entrypoint_file_bytes'] = _MAX_ENTRYPOINT_FILE_BYTES
    try:
        patterns = (*_exclusions(), *_CODE_PRIVATE)
        root, paths, candidates, repo_limits = (_repository(repository_path, patterns, inventory=inventory, requested=focus)
                                                if focus else _repository(repository_path, patterns, inventory=inventory))
    except (EvidenceReadError, OSError) as exc:
        freshness_unknown = True
        patterns = ()
        limitations.add(str(exc) if isinstance(exc, EvidenceReadError) else 'The repository is unavailable.')
    limitations.update(repo_limits)
    path_ranks = {}
    requested_paths, named_files = set(), set()
    scoped_migrations = set()
    if focus:
        path_ranks = {relative: focus.path_rank(relative) for relative in paths}
        requested_paths = {relative for relative, (exact, _) in path_ranks.items() if exact}
        named_files = {relative for relative in requested_paths if named_file_kind(relative)
                       or PurePosixPath(relative).suffix.casefold() == '.in' and not _manifest(relative)}
        if focus.terms & _MIGRATION_SCOPE_WORDS:
            migration_paths = sorted(relative for relative in paths if _python_migration(relative))
            half = _MAX_FOCUSED_MIGRATIONS // 2
            scoped_migrations = set(migration_paths[:half] + migration_paths[-half:])

        def focus_order(relative):
            exact, rank = path_ranks[relative]
            return -exact, _historical_path(relative), -rank, relative not in scoped_migrations

        promoted = [relative for relative in paths if any(path_ranks[relative])
                    and (_manifest(relative) or _scheme_category(relative) in {'code', 'docs', 'tests'}
                         or relative in named_files
                         or path_ranks[relative][0] and PurePosixPath(relative).suffix.lower() in _CODE_EXTENSIONS)]
        promoted.extend(sorted(scoped_migrations))
        candidates = sorted((path for path in dict.fromkeys([*candidates, *promoted])
                             if not focus.shadowed_basename(path)), key=focus_order)
        paths.sort(key=focus_order)
    else:
        migrations = sorted(relative for relative in paths if _python_migration(relative))
        # A small schema sample and explicit index migrations share the same
        # inspection budget as application code.
        migration_candidates = sorted(migrations, key=lambda path: (
            not re.search(r'(?:^|[_-])(?:fts\d*|search|index)(?:[_.-]|$)', PurePosixPath(path).name, re.I),
            path))[:16]
        scoped_migrations.update(migration_candidates)
        candidates = list(dict.fromkeys([*candidates[:8], *migration_candidates, *candidates[8:]]))
    eligible = set(candidates)
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW) if root else None
    except OSError:
        root_fd = None
        freshness_unknown = True
        limitations.add('The repository became unavailable during inspection.')
    try:
        repository_identity = (dependency_repository_identity(project_id, root, os.fstat(root_fd))
                               if root_fd is not None and not focus else None)
        # Included requirement files are selected from the same safe bounded path list.
        # All requirement candidates participate in markers, including not-yet-expanded includes.
        dependency_paths = paths
        markers, metadata_stop = _markers(root_fd, paths) if root_fd is not None else ([], None)
        file_omission_reasons = {}
        if metadata_stop:
            limitations.add(f'The repository metadata {metadata_stop.replace("_", " ")} was reached; some paths were omitted.')
            marked_paths = {relative for relative, _ in markers}
            file_omission_reasons.update(dict.fromkeys(eligible - marked_paths, f'metadata_{metadata_stop}'))
            paths = [relative for relative in paths if relative in marked_paths]
            candidates = [relative for relative in candidates if relative in marked_paths]
        scope_identity = {'scope': focus.value, 'scope_policy': 18, 'requested_paths': sorted(focus.paths),
                          'selected_named_paths': sorted(requested_paths),
                          'excluded_references': sorted(focus.excluded_references)} if focus else {}
        key = _digest({'namespace': 'scheme-inspection-v16' if focus else 'scheme-inspection-v34', 'project_id': project_id,
            'published_knowledge': knowledge['marker'],
            'budgets': policy, 'inventory': inventory,
            'repository': repository_path, 'patterns': patterns, 'markers': markers,
            'records': records, 'sessions': session_markers, 'limits': sorted(limitations),
            'freshness_unknown': freshness_unknown, **scope_identity,
            **({'dependency_repository': repository_identity} if not focus else {})})
        with Session(engine) as db:
            cached = db.get(ProjectHistoryBatch, (project_id, key))
            if not metadata_stop and cached is not None and isinstance(cached.evidence, list) and len(cached.evidence) == 1:
                result = deepcopy(cached.evidence[0])
                result['diagnostics'].update(cache_hits=1, duration_ms=round((time.monotonic() - started) * 1000))
                return result
        dependency_inventory, imported_names, dependency_stop = (
            (None, set(), None) if focus else _dependency_inventory(
                root_fd, dependency_paths, bool(repository_path)))
        if repository_identity is not None:
            dependency_inventory['repository_identity'] = repository_identity
        sources, dependencies = list(records), []
        scanned = set()
        files_read = 0
        decoded_reads = 0
        omitted_reasons = {}
        inspection_stop = None
        budget = _InspectionBudget(read_budget)
        marker_by_path = dict(markers)
        # A vanished repository has no directory descriptor to anchor safe reads.
        pending = list(candidates) if root_fd is not None else []
        shared_stems = _shared_feature_stems(paths)
        primary_stems = {'main', 'app', 'index', 'server', 'cli'}
        if not focus:
            primary_stems.update(('router', 'routes'))
        priorities = {path: ((0, i) if PurePosixPath(path).stem.casefold() in primary_stems
                             else (1, -1) if PurePosixPath(path).stem.casefold() in shared_stems
                             else (99, i)) for i, path in enumerate(candidates)}
        last_area = None
        # Directory names set initial priority, not whether explicitly imported
        # code can participate in the application. Resolve imports against every
        # safe code path; unreferenced backups remain outside initial candidates.
        code_paths = {path for path in paths if _scheme_category(path) == 'code'
                      and (not focus or not focus.shadowed_basename(path))}
        requirement_paths = {path for path in candidates if PurePosixPath(path).name.lower().startswith('requirements')}
        entrypoint_paths = set()
        declared_modules = {}
        entrypoint_functions = {}
        reachable_paths = set()
        handoff_paths = set()
        route_targets = set()
        route_queries = set()
        route_callers = {}
        python_prefixes = {}
        prefix_chars = 0
        direct_handoffs = {}
        configuration_paths = set()
        configurations = {}
        if not focus:
            for path in paths:
                if (PurePosixPath(path).suffix.casefold() in {'.in', '.mdp'}
                        and not _manifest(path) and not _historical_path(path)):
                    configurations.setdefault(PurePosixPath(path).name, []).append(path)
        declared_commands, guide_words = set(), {}
        while pending:
            try:
                budget.check()
            except _InspectionStopped as exc:
                inspection_stop = exc.reason
                omitted_reasons[exc.reason] = len(set(pending) - scanned)
                break
            pick = 0
            declared = [i for i, candidate in enumerate(pending) if candidate in entrypoint_paths]
            if focus and (any(path_ranks[pending[0]]) or pending[0] in scoped_migrations):
                # Do not let general entrypoints displace an explicitly named
                # or scope-matching file before the byte/time budget is spent.
                pick = 0
            elif declared:
                pick = min(declared, key=lambda i: priorities[pending[i]])
            elif pending and _scheme_category(pending[0]) == 'code' and PurePosixPath(pending[0]).name != 'setup.py':
                choices = [i for i, candidate in enumerate(pending)
                           if _area(candidate) != last_area and _scheme_category(candidate) == 'code']
                if not choices:
                    choices = [i for i, candidate in enumerate(pending) if _scheme_category(candidate) == 'code']
                pick = min(choices, key=lambda i: priorities.get(pending[i], (99, i)))
            relative = pending.pop(pick)
            if _scheme_category(relative) == 'code':
                last_area = _area(relative)
            if relative in scanned:
                continue
            scanned.add(relative)
            try:
                is_code = (_scheme_category(relative) == 'code'
                           or relative in entrypoint_paths and PurePosixPath(relative).suffix == '.py'
                           or not focus and relative in scoped_migrations
                           or focus and (_scheme_category(relative) == 'tests'
                               or path_ranks[relative][0] and PurePosixPath(relative).suffix.lower() in _CODE_EXTENSIONS))
                marker = marker_by_path.get(relative)
                if relative in named_files and not is_code and isinstance(marker, list) and marker[3] > _MAX_BYTES:
                    file_omission_reasons[relative] = 'requested_file_too_large'
                    omitted_reasons['requested_file_too_large'] = omitted_reasons.get('requested_file_too_large', 0) + 1
                    limitations.add('Some named files exceeded the 64 KiB per-file inspection limit.')
                    continue
                named_python = bool(focus and relative in requested_paths and PurePosixPath(relative).suffix == '.py')
                extended = named_python or (not focus and PurePosixPath(relative).suffix == '.py'
                                            and relative in (entrypoint_paths | reachable_paths | route_targets))
                file_limit = _MAX_ENTRYPOINT_FILE_BYTES if extended else _MAX_BYTES
                value = _read(root_fd, relative, allow_prefix=is_code, budget=budget,
                              **({'max_file_bytes': file_limit} if extended else {}))
                decoded_reads += 1
                declarations = _entrypoint_declarations(value, relative, preserve_order=not focus)
                declared_commands.update(command for command, _ in declarations)
                is_guide = _scheme_category(relative) == 'docs'
                if is_guide:
                    guide_words[relative] = set(re.findall(r'[A-Za-z][A-Za-z0-9_.-]*', value))
                targets = _entrypoint_targets(declarations, relative, paths)
                if any(not _entrypoint_targets([declaration], relative, paths) for declaration in declarations):
                    limitations.add('Some declared entrypoint modules were unavailable within the safe bounded repository paths.')
                for order, target in enumerate(targets):
                    declared_modules.setdefault(target, len(declared_modules))
                    eligible.add(target)
                    entrypoint_paths.add(target)
                    priorities[target] = (-1, order)
                    if target not in scanned and target not in pending:
                        pending.append(target)
                if not focus:
                    for declaration in declarations:
                        for target in _entrypoint_targets([declaration], relative, code_paths | entrypoint_paths):
                            entrypoint_functions.setdefault(target, set()).add(declaration[1].split(':', 1)[1])
                    if is_guide and not _historical_path(relative):
                        documented, _ = _code_hints(relative, value, code_paths)
                        for target in documented:
                            if not _production_path(target):
                                continue
                            declared_commands.add(PurePosixPath(target).name)
                            entrypoint_paths.add(target)
                            eligible.add(target)
                            priorities[target] = (-1, len(entrypoint_paths))
                            if target not in scanned and target not in pending:
                                pending.append(target)
                    if configurations and (is_code or is_guide) and not _historical_path(relative):
                        for name in dict.fromkeys(_WORKFLOW_CONFIG.findall(value)):
                            matches = configurations.get(name, ())
                            if len(matches) > 1 and _production_path(relative):
                                matches = [path for path in matches if not any(
                                    part.casefold() in {'test', 'tests', 'fixtures', 'example', 'examples', 'docs'}
                                    for part in PurePosixPath(path).parts[:-1])]
                            if len(matches) != 1:
                                continue
                            target = matches[0]
                            configuration_paths.add(target)
                            eligible.add(target)
                            if target not in scanned and target not in pending:
                                pending.insert(0, target)
                declared_excerpt = '\n'.join(f'Declared entrypoint {command} = {target}' for command, target in declarations)
                admitted_handoffs = []
                if is_code:
                    # Metadata already participates in the cache key; a large file
                    # contributes only a bounded prefix, never an implied full scan.
                    truncated = isinstance(marker, list) and marker[3] > file_limit
                    if truncated:
                        limitations.add(f'Some source files were limited to a {file_limit // 1024} KiB prefix; later implementation may be omitted.')
                    called_names = set()
                    returned_calls = {}
                    support_names = set()
                    roots = sorted(entrypoint_functions.get(relative, ()))
                    syntax = (_structural_excerpt(value, relative, focus=focus.score, named_python=named_python,
                                                  admitted_handoffs=admitted_handoffs, truncated=truncated)
                              if focus else _structural_excerpt(value, relative,
                                  entrypoints=roots, trace_imports=called_names,
                                  route_imports=returned_calls, route_support=support_names, truncated=truncated))
                    if (not focus and PurePosixPath(relative).suffix == '.py'
                            and prefix_chars + len(value) <= DEFAULT_SCHEME_READ_BYTES):
                        python_prefixes[relative] = (value, declared_excerpt, roots, truncated)
                        prefix_chars += len(value)
                    if not focus and called_names:
                        entrypoint_paths.add(relative)
                    linked = _import_targets(value, relative, code_paths)
                    if support_names:
                        # Ambiguous bindings can guide query-source selection
                        # without making the imported function a verified root.
                        route_queries.update(_import_targets(value, relative, code_paths, called_names=support_names))
                    if returned_calls:
                        for name in sorted(returned_calls):
                            targets = _import_targets(value, relative, code_paths, called_names={name})
                            route_targets.update(targets)
                            if len(targets) == 1 and PurePosixPath(targets[0]).suffix == '.py':
                                entrypoint_functions.setdefault(targets[0], set()).add(returned_calls[name])
                                route_callers.setdefault(targets[0], relative)
                    if not focus and called_names:
                        reachable_paths.update(_import_targets(value, relative, code_paths, called_names=called_names))
                        visible_calls = {name for line in syntax.splitlines() if 'Python entrypoint flow ' in line
                                         for name in re.findall(r'\b([A-Za-z_]\w*)(?:\.[A-Za-z_]\w*)*\(', line)}
                        direct = _import_targets(value, relative, code_paths, called_names=called_names & visible_calls)
                        direct_handoffs[relative] = direct
                        handoff_paths.update(direct)
                    for order, target in enumerate(linked):
                        eligible.add(target)
                        if target not in scanned:
                            candidate_priority = (priorities.get(relative, (99, 0))[0] + 1, order)
                            priorities[target] = min(priorities.get(target, candidate_priority), candidate_priority)
                            if target not in pending:
                                pending.append(target)
                files_read += 1
                source_id = 'repository:file:' + hashlib.sha256(relative.encode()).hexdigest()[:24]
                includes = []
                manifest_excerpts = None
                if _manifest(relative) or relative in requirement_paths:
                    before = len(dependencies)
                    if relative in requirement_paths:
                        _requirements(value, relative, source_id, dependencies, includes, limitations)
                    else:
                        _parse_manifest(value, relative, source_id, dependencies, includes, limitations)
                    # Preserve safe normalized declarations, not package-manager credentials/URLs.
                    # Deduplicate exact declarations in linear time before chunk
                    # references are assigned; different constraints keep distinct IDs.
                    dependencies[before:] = list({item['id']: item for item in dependencies[before:]}.values())
                    manifest_excerpts = _manifest_excerpts(declared_excerpt, dependencies[before:], source_id)
                    excerpt = ''
                    provenance = 'dependency manifest'
                    for include in includes:
                        if include in paths and _safe_relative(include, patterns) and include not in scanned:
                            eligible.add(include)
                            requirement_paths.add(include)
                            pending.insert(0, include)
                        else:
                            limitations.add('Some requirement includes were excluded or outside the bounded repository paths.')
                elif is_code:
                    excerpt = '\n'.join(part for part in (declared_excerpt, syntax) if part)
                    provenance = 'repository test source' if _scheme_category(relative) == 'tests' else 'observed in code'
                elif relative in configuration_paths or relative in named_files and PurePosixPath(relative).suffix.casefold() == '.in':
                    excerpt_config = mdp_excerpt if PurePosixPath(relative).suffix.casefold() == '.mdp' else namelist_excerpt
                    excerpt = excerpt_config(value, limit=_MAX_EXCERPT)
                    provenance = 'repository configuration'
                elif relative in named_files:
                    excerpt = named_file_excerpt(value, relative, limit=_MAX_EXCERPT, priority=focus.score)
                    provenance = 'repository file structure'
                    if excerpt:
                        limitations.add('Named build, configuration, and SQL files supply bounded structural excerpts with values omitted.')
                else:
                    if focus:
                        selected = select_passages(value, _MAX_EXCERPT, priority=focus.score)
                        excerpt, omitted = selected['text'], selected['omitted']
                    else:
                        excerpt, omitted = _documentation_excerpt(value)
                    if omitted:
                        limitations.add('Some documentation sections were omitted from bounded excerpts; omitted text is not evidence of absence.')
                    provenance = 'project documentation'
                if manifest_excerpts is not None:
                    for chunk_id, chunk in manifest_excerpts:
                        sources.append(_source(chunk_id, relative, chunk, provenance, path=relative,
                                               revision=hashlib.sha256(value.encode()).hexdigest()))
                elif excerpt:
                    if len(excerpt) > 3200:
                        limitations.add('Some normalized manifest declaration excerpts were truncated.')
                    sources.append(_source(source_id, relative, excerpt[:3200], provenance, path=relative,
                                           revision=hashlib.sha256(value.encode()).hexdigest(),
                                           **({'local_handoffs': admitted_handoffs} if admitted_handoffs
                                              and provenance == 'observed in code' and len(excerpt) <= 3200 else {})))
            except _InspectionStopped as exc:
                inspection_stop = exc.reason
                omitted_reasons[exc.reason] = 1 + len(set(pending) - scanned)
                file_omission_reasons[relative] = exc.reason
                break
            except OSError:
                omitted_reasons['unsafe_or_unavailable'] = omitted_reasons.get('unsafe_or_unavailable', 0) + 1
                file_omission_reasons[relative] = 'unsafe_or_unavailable'
                freshness_unknown = True
                limitations.add('Some selected files could not be read safely; their freshness is unknown.')
            except (ValueError, TypeError, AttributeError, RecursionError):
                omitted_reasons['unreadable_or_malformed'] = omitted_reasons.get('unreadable_or_malformed', 0) + 1
                file_omission_reasons[relative] = 'unreadable_or_malformed'
                limitations.add('Some selected files were unreadable, unsafe, malformed, binary, above 64 KiB, or too deeply nested.')
        for source in sources[len(records):]:
            relative = source['path']
            if relative not in python_prefixes or source['provenance'] != 'observed in code':
                continue
            value, declared_excerpt, previous_roots, truncated = python_prefixes[relative]
            roots = sorted(entrypoint_functions.get(relative, ()))
            if roots == previous_roots:
                continue
            if time.monotonic() >= budget.deadline:
                limitations.add('The inspection time limit prevented some late-discovered entrypoint traces.')
                break
            # A route or declaration may establish an entrypoint after its
            # target was inspected. Reuse the already-read bounded prefix.
            syntax = _structural_excerpt(value, relative, entrypoints=roots, trace_imports=set(), route_imports={},
                                         truncated=truncated)
            source['text'] = '\n'.join(part for part in (declared_excerpt, syntax) if part)[:3200]
            source['content_hash'] = hashlib.sha256(source['text'].encode()).hexdigest()
            entrypoint_paths.add(relative)
        inspection_seconds = round(time.monotonic() - budget.started, 6)
        if not inspection_stop and inspection_seconds >= _INSPECTION_SECONDS:
            inspection_stop = 'time_budget'
            omitted_reasons['time_budget'] = len(set(pending) - scanned)
        if inspection_stop:
            file_omission_reasons.update(dict.fromkeys(set(pending) - scanned, inspection_stop))
            limitations.add(f'The source inspection {inspection_stop.replace("_", " ")} was reached; coverage may be incomplete.')
        # Prevent an unusually large manifest/dependency set from exhausting the AI input budget.
        bounded, size = [], 0
        included_ids = set()
        source_by_id = {source['id']: source for source in sources}

        def add_source(source, limit):
            nonlocal size
            support = source.get('supporting_source_ids', [])
            if any(identifier not in source_by_id for identifier in support):
                return False
            bundle = [source, *(source_by_id[identifier] for identifier in support)]
            additions = {entry['id']: entry for entry in bundle if entry['id'] not in included_ids}
            required = sum(len(entry['text']) for entry in additions.values())
            if size + required > limit:
                return False
            bounded.extend(additions.values())
            included_ids.update(additions)
            size += required
            return True
        command_guides = {path for path, words in guide_words.items() if words & declared_commands}
        repository_sources = sorted(sources[len(records):], key=lambda source: (
            _historical_path(source.get('path', '')),
            source.get('path') not in entrypoint_paths,
            PurePosixPath(source.get('path', '')).name not in {'setup.py', 'pyproject.toml'},
            not _manifest(source.get('path', '')),
            source.get('path') not in command_guides))
        if not focus:
            repository_sources = _general_repository_order(repository_sources, entrypoint_paths, declared_modules,
                                                           handoff_paths, reachable_paths, configuration_paths, command_guides,
                                                           route_targets)
        # Reserve half for repository evidence when both kinds are present. Keep
        # whole excerpts and return any unused repository allowance to records.
        repository_reserve = min(source_budget // 2, sum(len(s['text']) for s in repository_sources))
        if focus:
            schema_sources = []
            if scoped_migrations:
                for source in repository_sources:
                    if source.get('path') not in scoped_migrations:
                        continue
                    statements = '\n'.join(re.findall(r'(?m)^L\d+: ((?:CREATE|ALTER) .+)$', source['text']))
                    if (score := focus.score(statements)):
                        schema_sources.append((-score, source['id']))
            schema_refs = {identifier for _, identifier in sorted(schema_sources)[:_MAX_FOCUSED_SCHEMA_SOURCES]}
            structural_refs = schema_refs | _scoped_structural_source_refs(repository_sources, focus)
            priority_order = sorted([*repository_sources, *records],
                                    key=lambda source: focus.source_key(source, structural_evidence=source['id'] in structural_refs))
        else:
            deferred = []
            for source in records:
                if not add_source(source, source_budget - repository_reserve):
                    deferred.append(source)
            workflow_limit = size + repository_reserve // 4
            for workflow in _index_workflows(repository_sources, preferred_paths=route_queries):
                missing = [source for source in workflow if source['id'] not in included_ids]
                if size + sum(len(source['text']) for source in missing) <= workflow_limit:
                    for source in missing:
                        add_source(source, workflow_limit)
            config_limit = size + repository_reserve // 8
            for source in repository_sources:
                if source['path'] in configuration_paths:
                    add_source(source, config_limit)
            lifecycle_limit = size + repository_reserve // 4
            by_path = {source['path']: source for source in repository_sources}
            for implementation in repository_sources:
                caller = by_path.get(route_callers.get(implementation['path']))
                if caller is None or not any(marker in implementation['text']
                                             for marker in ('Workflow call ', 'Workflow artifact call ')):
                    continue
                group = [caller, implementation]
                if 'Workflow artifact call ' in implementation['text']:
                    related = next((by_path[path] for path in direct_handoffs.get(caller['path'], ())
                                    if path != implementation['path'] and path in by_path
                                    and re.search(r'Workflow artifact call [^\n]*\bread_\w+\(', by_path[path]['text'])), None)
                    if related:
                        group.append(related)
                missing = [source for source in group if source['id'] not in included_ids]
                if size + sum(len(source['text']) for source in missing) <= lifecycle_limit:
                    for source in missing:
                        add_source(source, lifecycle_limit)
                    break
            # Preserve independent user-facing commands alongside a detailed
            # handoff so that one workflow cannot crowd out the other branches.
            command_limit = size + repository_reserve // 6
            for path in declared_modules:
                if path in by_path:
                    add_source(by_path[path], command_limit)
            priority_order = [*(source for source in repository_sources if source['path'] not in scoped_migrations),
                              *deferred]
        for source in priority_order:
            if not add_source(source, source_budget):
                omitted_reasons['source_output_budget'] = omitted_reasons.get('source_output_budget', 0) + 1
                limitations.add('The source excerpt budget was reached; some evidence was omitted.')
                continue
        if focus:
            # Normalized manifest excerpts can support a focused diagram. The
            # project-wide inventory and dependency explanations are separate.
            dependencies = []
        else:
            allowed_ids = {source['id'] for source in bounded}
            dependencies = [d for d in dependencies if set(d['source_ids']) <= allowed_ids]
            source_order = {source['id']: index for index, source in enumerate(bounded)}
            dependencies.sort(key=lambda dependency: (
                _dependency_name(dependency['name']) not in imported_names,
                min(source_order[ref] for ref in dependency['source_ids'])))
            if len(dependencies) > _MAX_DEPENDENCIES:
                dependencies = dependencies[:_MAX_DEPENDENCIES]
                limitations.add('Up to 100 dependency declarations are supplied for project-use explanations. The local dependency inventory is separate.')
        excerpt_count = sum(source['provenance'] == 'Codex conversation' for source in bounded)
        tree = {}
        for relative in paths[:_MAX_PATHS]:
            parts = PurePosixPath(relative).parts
            for n in range(1, len(parts)):
                tree['/'.join(parts[:n])] = 'directory'
            # Symlinks/nonregular files should not appear as inspectable code.
            if root_fd is not None:
                try:
                    fd = _descriptor(root_fd, relative)
                    os.close(fd)
                except OSError:
                    continue
            tree[relative] = 'file'
        result = {'sources': bounded, 'dependencies': dependencies, 'dependency_inventory': dependency_inventory,
            'knowledge_context': deepcopy(knowledge['metadata']),
            'tree': [{'path': path, 'type': kind} for path, kind in sorted(tree.items())],
            'coverage': {'partial': bool(limitations), 'freshness_unknown': freshness_unknown,
                         'limitations': sorted(limitations),
                         'inspection_stopped': inspection_stop, 'omitted_reason_counts': omitted_reasons,
                         'inspection_policy': policy,
                         'file_counts': {**inventory,
                             'eligible_files': len(eligible) if inventory['listing_complete'] else None,
                             'policy_omitted_paths': inventory['safe_paths'] - len(eligible) if inventory['listing_complete'] else None,
                             'attempted_files': len(scanned), 'read_files': decoded_reads,
                             'prefix_read_files': budget.prefix_read_files,
                             'failed_reads': len(scanned) - decoded_reads,
                             'not_attempted_files': len(eligible) - len(scanned) if inventory['listing_complete'] else None,
                             'evidence_files': 0, 'read_not_in_evidence': decoded_reads},
                         'files_inspected': files_read, 'conversation_excerpts': excerpt_count},
            'diagnostics': {'cache_hits': 0, 'input_chars': size, 'files_inspected': files_read,
                            'files_attempted': len(scanned), 'bytes_read': budget.bytes_read,
                            'inspection_seconds': inspection_seconds,
                            'omitted_reason_counts': omitted_reasons,
                            'conversation_excerpts': excerpt_count,
                            'duration_ms': round((time.monotonic() - started) * 1000)}}
        selected_paths = {source['path'] for source in bounded if source.get('path')}
        package_chars = _bound_package(result, package_budget)
        # Attach local accounting only after the AI package has been finalized.
        # A file with even one retained excerpt is counted as supplied.
        supplied_paths = {source['path'] for source in result['sources'] if source.get('path')}
        result['route_query_paths'] = sorted(route_queries & supplied_paths)
        if inventory['listing_complete']:
            prepared_paths = {source['path'] for source in repository_sources}
            omitted_paths = eligible - supplied_paths
            file_omission_reasons.update(dict.fromkeys(prepared_paths - selected_paths, 'source_output_budget'))
            file_omission_reasons.update(dict.fromkeys(selected_paths - supplied_paths, 'package_output_budget'))
            default_reason = 'unsafe_or_unavailable' if root_fd is None else 'not_attempted'
            result['coverage']['file_omissions'] = {
                'total': len(omitted_paths),
                'files': [
                    {'path': path, 'reason': file_omission_reasons.get(
                        path, 'no_usable_excerpt' if path in scanned else default_reason)}
                    for path in sorted(omitted_paths)[:_MAX_RECORDED_FILE_OMISSIONS]
                ],
            }
        if focus:
            requested_omissions = []
            for relative in sorted(requested_paths - supplied_paths):
                reason = file_omission_reasons.get(relative)
                if reason is None:
                    if relative not in eligible:
                        reason = 'unsupported_requested_type'
                    elif root_fd is None:
                        reason = 'repository_unavailable'
                    else:
                        reason = 'no_usable_excerpt' if relative in scanned else 'not_attempted'
                requested_omissions.append({'path': relative, 'reason': reason})
            requested_omissions.extend(focus.unresolved_references(
                requested_paths, root_fd is not None and inventory['listing_complete']))
            if requested_omissions:
                result['coverage']['requested_file_omissions'] = requested_omissions[:_MAX_RECORDED_FILE_OMISSIONS]
        result['diagnostics'].update(package_chars=package_chars,
            input_chars=sum(len(source['text']) for source in result['sources']),
            conversation_excerpts=result['coverage']['conversation_excerpts'],
            duration_ms=round((time.monotonic() - started) * 1000))
        # Local coverage accounting describes the inspection, not changes to its sources.
        fingerprint_coverage = {key: value for key, value in result['coverage'].items()
                                if key not in {'file_counts', 'inspection_policy', 'file_omissions'}}
        # The identity governs matching saved dependency purposes. Preserve
        # the existing graph freshness fingerprint across the metadata upgrade.
        fingerprint_inventory = ({key: value for key, value in dependency_inventory.items()
                                  if key != 'repository_identity'} if dependency_inventory is not None else None)
        result['fingerprint'] = _digest({key: result[key] for key in
            ('sources', 'dependencies', 'tree', 'knowledge_context')} | {
                'dependency_inventory': fingerprint_inventory, 'coverage': fingerprint_coverage,
                'sessions': session_markers, 'published_knowledge': knowledge['marker'], **scope_identity})
        if persist_cache and inspection_stop != 'time_budget' and dependency_stop != 'time_budget' and not metadata_stop:
            with Session(engine) as db:
                db.execute(text('BEGIN IMMEDIATE'))
                if (db.get(Project, project_id) is not None
                        and db.get(ProjectHistoryBatch, (project_id, key)) is None):
                    db.add(ProjectHistoryBatch(project_id=project_id, fingerprint=key, evidence=[result]))
                    db.commit()
        return result
    finally:
        if root_fd is not None:
            os.close(root_fd)
