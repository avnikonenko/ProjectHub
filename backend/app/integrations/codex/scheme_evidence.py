"""Bounded Scheme inputs from local files and stored records; never runs project code or AI."""
import ast
from copy import deepcopy
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import stat
import time
import tomllib

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from ...models import ProjectHistoryBatch
from ...project_evidence import (
    EvidenceReadError, _CODE_PRIVATE, _code_category, _code_order, _digest,
    _excluded, _exclusions, _git, _repository_root,
)
from ...services import require_project
from .scheme_passages import GAP, READ_CHARS, EXCERPT_CHARS, select_passages

_MAX_PATHS = 300
_MAX_FILES = 24
_MAX_MANIFESTS = 12
_MAX_BYTES = 65536
_MAX_EXCERPT = 1600
_MAX_DEPENDENCIES = 100
_MAX_SOURCES_CHARS = 40_000
_REQUIREMENT = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9_,.-]+\])?(.*)$')


def _source(identifier, label, value, provenance, **metadata):
    return {'id': identifier, 'label': label[:240], 'text': value,
            'provenance': provenance, 'content_hash': hashlib.sha256(value.encode()).hexdigest(), **metadata}


def _safe_relative(value, patterns):
    path = PurePosixPath(value)
    return (bool(value) and not path.is_absolute() and '..' not in path.parts
            and not any(ord(c) < 32 for c in value) and not _excluded(value, patterns))


def _manifest(path):
    name = PurePosixPath(path).name.lower()
    return name in ('pyproject.toml', 'package.json') or (
        name.startswith('requirements') and name.endswith(('.txt', '.in')))


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


def _read(root_fd, relative, *, allow_prefix=False):
    fd = _descriptor(root_fd, relative)
    try:
        data = bytearray()
        while len(data) <= _MAX_BYTES:
            chunk = os.read(fd, min(8192, _MAX_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if b'\0' in data:
            raise ValueError('Binary file')
        if len(data) > _MAX_BYTES:
            if not allow_prefix:
                raise ValueError('Oversized file')
            data = data[:_MAX_BYTES].rsplit(b'\n', 1)[0]
        return data.decode('utf-8')
    finally:
        os.close(fd)


def _markers(root_fd, candidates):
    result = []
    for relative in candidates:
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
    return result


_AUXILIARY = {'script', 'scripts', 'migration', 'migrations', 'test', 'tests',
              'tool', 'tools', 'benchmark', 'benchmarks', 'example', 'examples', 'docs'}
_ENTRYPOINTS = {'main', 'app', 'index', 'server', 'cli', 'router', 'routes', 'services', 'models'}
_CODE_EXTENSIONS = ('.py', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs')


def _production_path(relative):
    path = PurePosixPath(relative)
    return _code_category(relative) == 'code' and not any(part.lower() in _AUXILIARY for part in path.parts[:-1])


def _scheme_order(relative):
    path = PurePosixPath(relative)
    category = _code_category(relative)
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


def _import_targets(value, relative, available):
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
        candidates = [base.as_posix(), *((str(base) + ext) for ext in _CODE_EXTENSIONS),
                      *((base / ('index' + ext)).as_posix() for ext in _CODE_EXTENSIONS),
                      (base / '__init__.py').as_posix()]
        for candidate in candidates:
            if candidate in available and candidate != relative:
                score = (symbol not in feature_names,
                         PurePosixPath(candidate).stem.casefold() not in {'router', 'routes', 'app', 'main', 'index', 'server'})
                found[candidate] = min(found.get(candidate, score), score)
    return sorted(found, key=lambda candidate: (*found[candidate], *_scheme_order(candidate)))


def _repository(repository_path, patterns):
    if not repository_path:
        return None, [], [], ['No repository is linked; diagrams can use saved discussions only.']
    deadline = time.monotonic() + 5
    root = _repository_root(repository_path, deadline)
    listed, _ = _git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', deadline=deadline)
    paths = sorted({os.fsdecode(v) for v in listed.split(b'\0') if v}, key=_code_order)
    # Git's exclude rules apply to untracked files; explicit tracked ignored files are excluded too.
    ignored, _ = _git(root, 'ls-files', '-z', '--cached', '--ignored', '--exclude-standard', deadline=deadline)
    ignored_paths = {os.fsdecode(v) for v in ignored.split(b'\0') if v}
    paths = [v for v in paths if v not in ignored_paths and _safe_relative(v, patterns)]
    limits = []
    if len(paths) > _MAX_PATHS:
        limits.append('Code structure is limited to 300 safe paths; other paths were omitted.')
    paths = sorted(paths, key=_scheme_order)[:_MAX_PATHS]
    manifests = sorted((v for v in paths if _manifest(v)), key=_code_order)[:_MAX_MANIFESTS]
    declarations = [v for v in paths if PurePosixPath(v).name == 'setup.py'][:3]
    docs = sorted((v for v in paths if _code_category(v) == 'docs'), key=_code_order)[:3]
    shared_stems = _shared_feature_stems(paths)
    code = sorted((v for v in paths if _code_category(v) == 'code' and v not in declarations), key=lambda v: (
        PurePosixPath(v).stem.casefold() not in _ENTRYPOINTS,
        PurePosixPath(v).stem.casefold() not in shared_stems, *_scheme_order(v)))
    production = [v for v in code if _production_path(v)]
    if production:
        code = production
    # Round-robin top-level areas so a backend does not hide a frontend.
    buckets = {}
    for value in code:
        buckets.setdefault(_area(value), []).append(value)
    chosen = []
    while any(buckets.values()) and len(chosen) < _MAX_FILES - len(manifests) - len(declarations) - len(docs):
        for values in buckets.values():
            if values and len(chosen) < _MAX_FILES - len(manifests) - len(declarations) - len(docs):
                chosen.append(values.pop(0))
    candidates = [*manifests, *declarations, *docs, *chosen]
    if len(candidates) < len(paths):
        limits.append('Only selected source files and bounded declaration excerpts were inspected.')
    return root, paths, candidates, limits


def _entrypoint_declarations(value, relative):
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
    return sorted(set(result))


def _entrypoint_targets(declarations, relative, available):
    targets = []
    for _, target in declarations:
        module = PurePosixPath(*target.split(':', 1)[0].split('.'))
        for base in (PurePosixPath(relative).parent, PurePosixPath(relative).parent / 'src'):
            for candidate in (str(base / module) + '.py', (base / module / '__init__.py').as_posix()):
                if candidate in available and candidate not in targets:
                    targets.append(candidate)
    return targets


def _documentation_excerpt(value):
    """Prefer a whole feature section over decorative README prefixes."""
    heading = re.search(r'(?im)^(#{1,6})[ \t]+(?:features?|capabilities)[ \t]*:?[ \t]*$', value)
    if heading is None:
        return value[:_MAX_EXCERPT], len(value) > _MAX_EXCERPT
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
    if not isinstance(name, str) or not re.fullmatch(r'[@A-Za-z0-9][@A-Za-z0-9._/-]{0,159}', name):
        limitations.add('Some malformed dependency declarations were omitted.')
        return
    if not isinstance(constraint, str) or len(constraint) > 300 or any(ord(c) < 32 for c in constraint):
        limitations.add('Some malformed dependency declarations were omitted.')
        return
    # Direct URLs, credentials, and local machine paths must not be echoed into diagrams.
    if any(token in constraint.lower() for token in ('://', 'git+', 'file:', ' @ ', '\\')) or constraint.startswith(('/', '~', '@')):
        constraint = 'Direct/local reference (location omitted)'
    identifier = 'dependency:' + _digest([name, category, source_id])[:20]
    dependencies.append({'id': identifier, 'name': name, 'constraint': constraint,
                         'category': category, 'source_ids': [source_id], 'purpose': ''})


def _requirements(value, path, source_id, dependencies, includes, limitations):
    for line in value.splitlines()[:500]:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith(('-r ', '--requirement ')):
            include = line.split(None, 1)[1].strip()
            candidate = (PurePosixPath(path).parent / include).as_posix()
            includes.append(candidate)
            continue
        if line.startswith('-'):
            limitations.add('Requirements options and constraints-file semantics are not evaluated.')
            continue
        match = _REQUIREMENT.fullmatch(line.split(' #', 1)[0])
        if match:
            name, extras, constraint = match.groups()
            category = 'development' if any(t in path.lower() for t in ('dev', 'test')) else 'runtime'
            _dependency(name, (extras or '') + constraint.strip(), category, source_id, dependencies, limitations)
        else:
            limitations.add('Some requirements declarations could not be parsed.')


def _parse_manifest(value, path, source_id, dependencies, includes, limitations):
    if PurePosixPath(path).name.lower() == 'package.json':
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError('Invalid package manifest')
        for field, category in (('dependencies', 'runtime'), ('devDependencies', 'development'),
                                ('optionalDependencies', 'optional'), ('peerDependencies', 'peer')):
            entries = parsed.get(field, {})
            if not isinstance(entries, dict):
                raise ValueError('Invalid dependency map')
            if len(entries) > _MAX_DEPENDENCIES:
                limitations.add('Some dependency declarations exceeded the extraction limit and were omitted.')
            for name, constraint in list(entries.items())[:_MAX_DEPENDENCIES]:
                _dependency(name, constraint, category, source_id, dependencies, limitations)
    elif PurePosixPath(path).name.lower() == 'pyproject.toml':
        parsed = tomllib.loads(value)
        project = parsed.get('project', {})
        groups = [('runtime', project.get('dependencies', []))]
        for category, entries in project.get('optional-dependencies', {}).items():
            groups.append(('optional:' + category, entries))
        for category, entries in parsed.get('dependency-groups', {}).items():
            groups.append(('development:' + category, entries))
        for category, entries in groups:
            if not isinstance(entries, list):
                raise ValueError('Invalid Python dependency group')
            if len(entries) > _MAX_DEPENDENCIES:
                limitations.add('Some dependency declarations exceeded the extraction limit and were omitted.')
            for entry in entries[:_MAX_DEPENDENCIES]:
                match = _REQUIREMENT.fullmatch(entry) if isinstance(entry, str) else None
                if match:
                    name, extras, constraint = match.groups()
                    _dependency(name, (extras or '') + constraint.strip(), category, source_id, dependencies, limitations)
                else:
                    limitations.add('Dynamic Python dependency groups are not evaluated.')
        if parsed.get('tool', {}).get('poetry') or 'dependencies' in project.get('dynamic', []):
            limitations.add('Poetry and dynamic Python declarations are not supported in this MVP.')
    else:
        _requirements(value, path, source_id, dependencies, includes, limitations)


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


def _call_summary(names, preferred=()):
    def priority(name):
        leaf = name.rsplit('.', 1)[-1]
        return (name in _ROUTINE_CALLS or leaf in {'append', 'extend', 'strip', 'get', 'items', 'values', 'keys', 'join'},
                name not in preferred, leaf.startswith(('use', 'set')), name.startswith('_'))
    return ', '.join(sorted(names, key=priority)[:12])


def _structural_excerpt(value, relative):
    """Balanced declarations/imports/static call names; no argument or initializer literals."""
    imports, declarations, calls = [], [], []
    if PurePosixPath(relative).suffix.lower() == '.py':
        try:
            parsed = ast.parse(value)
        except (SyntaxError, ValueError, RecursionError):
            parsed = None
        if parsed is not None:
            class Visitor(ast.NodeVisitor):
                scope = []
                observed_calls = {}

                def visit_Import(self, node):
                    imports.append((node.lineno, 'import ' + ', '.join(alias.name for alias in node.names)))

                def visit_ImportFrom(self, node):
                    imports.append((node.lineno, 'from ' + '.' * node.level + (node.module or '')
                                    + ' import ' + ', '.join(alias.name for alias in node.names)))

                def visit_FunctionDef(self, node):
                    declarations.append((node.lineno, 'def ' + node.name + '(...)'))
                    self.scope.append(node.name)
                    # Defaults/decorators are not function-body runtime evidence.
                    for child in node.body:
                        self.visit(child)
                    self.scope.pop()

                visit_AsyncFunctionDef = visit_FunctionDef

                def visit_ClassDef(self, node):
                    declarations.append((node.lineno, 'class ' + node.name))
                    self.scope.append(node.name)
                    for child in node.body:
                        self.visit(child)
                    self.scope.pop()

                def visit_Call(self, node):
                    name = _qualified_name(node.func)
                    if name and self.scope:
                        key = '.'.join(self.scope)
                        names = self.observed_calls.setdefault(key, [node.lineno, []])[1]
                        if name not in names and len(names) < 64:
                            names.append(name)
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
            calls = [(line, scope + ' uses call syntax: ' + _call_summary(names, preferred))
                     for scope, (line, names) in visitor.observed_calls.items()]
    if not declarations and not imports:
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
        function_marks = [(match.start(1), match[1]) for match in re.finditer(
            r'\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(', masked)]
        grouped_calls = {}
        for match in re.finditer(r'\b([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)(?:<[^<>;{}\n]{1,120}>)?\s*\(', masked):
            name = re.sub(r'\s+', '', match[1])
            if (name in seen_calls or name in {'if', 'for', 'while', 'switch', 'catch', 'function', 'import', 'super', 'return', 'async'}
                    or match.start(1) in declared_positions):
                continue
            seen_calls.add(name)
            scope = next((name for start, name in reversed(function_marks) if start < match.start()), 'module')
            number = value.count('\n', 0, match.start()) + 1
            group = grouped_calls.setdefault(scope, [number, []])
            if len(group[1]) < 64:
                group[1].append(name)
        calls = [(number, scope + ' lexical call syntax: ' + _call_summary(names))
                 for scope, (number, names) in grouped_calls.items()]
    # Public declarations and their call groups should not disappear behind long
    # import blocks or private helpers. Interleave groups within the same hard cap.
    declarations.sort(key=lambda item: (item[1].startswith('def _'), item[0]))
    calls.sort(key=lambda item: (item[1].startswith('_'), item[0]))
    buckets = [declarations, calls, imports]
    rendered = ['Static syntax only; call names do not prove runtime execution.']
    used = len(rendered[0]) + 1
    positions = [0, 0, 0]
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


def _records(db, project):
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
        limitations.append('Conversation coverage uses at most 12 recent stored excerpts; earlier discussion may be omitted.')
    if any(not row['history_indexed_at'] or not row['history_indexed_activity']
           or not row['codex_activity_at'] or row['history_indexed_activity'] != row['codex_activity_at']
           for row in sessions):
        limitations.append('Some linked conversations have no current saved index; no live Codex history was read.')
    turns = db.execute(text('SELECT h.id,h.session_id,h.turn_id,substr(h.content,1,:read_chars) AS excerpt,length(h.content) AS content_length '
        'FROM codex_history_turns h JOIN codex_sessions s ON s.id=h.session_id AND s.project_id=h.project_id '
        "WHERE h.project_id=:pid AND s.is_linked=1 AND h.status!='inProgress' ORDER BY h.id DESC LIMIT 12"),
        {'pid': project.id, 'read_chars': READ_CHARS}).mappings().all()
    notes = db.execute(text('SELECT id,substr(title,1,160) AS title,substr(content,1,:read_chars) AS content,kind,updated_at, '
                           'length(content) AS content_length,length(title) AS title_length '
                           'FROM notes WHERE project_id=:pid ORDER BY id DESC LIMIT 7'),
                       {'pid': project.id, 'read_chars': READ_CHARS}).mappings().all()
    # Bound discussion context independently so larger notes cannot crowd out all
    # repository evidence from the existing 40,000-character source package.
    turn_budget = 12_000 if notes else 24_000
    note_budget = 12_000 if turns else 24_000

    def passage(value, original_length, remaining):
        complete = value
        if original_length > len(value):
            # SQL's bounded read can end inside a paragraph or diagram. That
            # suffix is not a complete passage merely because it is end-of-input.
            boundaries = list(re.finditer(r'\n[ \t]*\n', value))
            complete = value[:boundaries[-1].end()] if boundaries else ''
        selected = select_passages(complete, min(EXCERPT_CHARS, max(0, remaining)))
        if selected['omitted']:
            limitations.append('Some discussion sections were omitted from bounded whole-passage selection; omitted text is not evidence of absence.')
        if original_length > len(value):
            limitations.append('Some discussions exceeded the 65,536-character inspection bound; later content was not inspected.')
        return selected, {'excerpt_spans': selected['spans'],
                          'inspected_hash': hashlib.sha256(value.encode()).hexdigest(),
                          'inspected_chars': len(value), 'original_chars': original_length}

    turn_sources = [_source(f"session:{row['session_id']}:turn:{row['turn_id']}",
        f"Session #{row['session_id']}, turn {row['turn_id']}", row['excerpt'], 'Codex conversation',
        session_id=row['session_id'], turn_id=row['turn_id'], original_chars=row['content_length'])
        for row in turns]
    # Confirm shared-prefix identity before charging the context budget; repeated
    # forks must not crowd out complementary turns that follow them.
    turn_sources = deduplicate_sources(db, project.id, turn_sources)
    excerpt_count = 0
    for source in turn_sources:
        selected, metadata = passage(source['text'], source['original_chars'], turn_budget)
        if selected['text']:
            sources.append({**source, **metadata, 'text': selected['text'],
                            'content_hash': hashlib.sha256(selected['text'].encode()).hexdigest()})
            turn_budget -= len(selected['text'])
            excerpt_count += 1
    if len(notes) > 6:
        limitations.append('Only the six most recent notes or imported documents were included.')
    if any(row['title_length'] > 160 for row in notes[:6]):
        limitations.append('Some note titles were truncated to 160 characters.')
    for row in notes[:6]:
        selected, metadata = passage(row['content'], row['content_length'], note_budget)
        if selected['text']:
            sources.append(_source(f"note:{row['id']}", row['title'], selected['text'],
                'imported discussion' if row['kind'] == 'document' else 'project note',
                date=row['updated_at'], **metadata))
            note_budget -= len(selected['text'])
    brief = db.execute(text('SELECT substr(sections,1,1800),generated_at,length(sections) FROM project_briefs WHERE project_id=:pid'),
                       {'pid': project.id}).first()
    if brief:
        if brief[2] > 1800:
            limitations.append('The previous AI summary was truncated to a 1,800-character excerpt.')
        sources.append(_source('brief:previous', 'Previous AI project summary (interpretation)', brief[0],
                               'prior AI interpretation', date=brief[1]))
    return sources, markers, limitations, excerpt_count


def collect_evidence(engine, project_id):
    """Inspect only bounded local inputs. The resulting snapshot preserves supplied evidence."""
    started = time.monotonic()
    limitations = set()
    freshness_unknown = False
    with Session(engine) as db:
        project = require_project(db, project_id)
        repository_path = project.repository_path
        objective = project.name
        try:
            records, session_markers, record_limits, excerpt_count = _records(db, project)
        except SQLAlchemyError:
            db.rollback()
            records = [_source('project:objective', 'Project name', objective, 'project metadata')]
            session_markers, excerpt_count = [], 0
            record_limits = ['Stored conversation or project knowledge evidence could not be read.']
            freshness_unknown = True
    freshness_unknown |= any('no current saved index' in value for value in record_limits)
    limitations.update(record_limits)
    root = None
    paths, candidates, repo_limits = [], [], []
    try:
        patterns = (*_exclusions(), *_CODE_PRIVATE)
        root, paths, candidates, repo_limits = _repository(repository_path, patterns)
    except (EvidenceReadError, OSError) as exc:
        freshness_unknown = True
        patterns = ()
        limitations.add(str(exc) if isinstance(exc, EvidenceReadError) else 'The repository is unavailable.')
    limitations.update(repo_limits)
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW) if root else None
    except OSError:
        root_fd = None
        freshness_unknown = True
        limitations.add('The repository became unavailable during inspection.')
    try:
        # Included requirement files are selected from the same safe bounded path list.
        # All requirement candidates participate in markers, including not-yet-expanded includes.
        markers = _markers(root_fd, paths) if root_fd is not None else []
        key = _digest({'namespace': 'scheme-inspection-v6', 'project_id': project_id,
            'repository': repository_path, 'patterns': patterns, 'markers': markers,
            'records': records, 'sessions': session_markers, 'limits': sorted(limitations),
            'freshness_unknown': freshness_unknown})
        with Session(engine) as db:
            cached = db.get(ProjectHistoryBatch, (project_id, key))
            if cached is not None and isinstance(cached.evidence, list) and len(cached.evidence) == 1:
                result = deepcopy(cached.evidence[0])
                result['diagnostics'].update(cache_hits=1, duration_ms=round((time.monotonic() - started) * 1000))
                return result
        sources, dependencies = list(records), []
        scanned = []
        files_read = 0
        pending = list(candidates)
        shared_stems = _shared_feature_stems(paths)
        priorities = {path: ((0, i) if PurePosixPath(path).stem.casefold() in {'main', 'app', 'index', 'server', 'cli'}
                             else (1, -1) if PurePosixPath(path).stem.casefold() in shared_stems
                             else (99, i)) for i, path in enumerate(candidates)}
        last_area = None
        code_paths = {path for path in paths if _production_path(path)}
        if not code_paths:
            code_paths = {path for path in paths if _code_category(path) == 'code'}
        requirement_paths = {path for path in candidates if PurePosixPath(path).name.lower().startswith('requirements')}
        entrypoint_paths = set()
        while pending and len(scanned) < _MAX_FILES:
            pick = 0
            declared = [i for i, candidate in enumerate(pending) if candidate in entrypoint_paths]
            if declared:
                pick = min(declared, key=lambda i: priorities[pending[i]])
            elif pending and _code_category(pending[0]) == 'code' and PurePosixPath(pending[0]).name != 'setup.py':
                choices = [i for i, candidate in enumerate(pending)
                           if _area(candidate) != last_area and _code_category(candidate) == 'code']
                if not choices:
                    choices = [i for i, candidate in enumerate(pending) if _code_category(candidate) == 'code']
                pick = min(choices, key=lambda i: priorities.get(pending[i], (99, i)))
            relative = pending.pop(pick)
            if _code_category(relative) == 'code':
                last_area = _area(relative)
            if relative in scanned:
                continue
            scanned.append(relative)
            try:
                is_code = (_code_category(relative) == 'code'
                           or relative in entrypoint_paths and PurePosixPath(relative).suffix == '.py')
                value = _read(root_fd, relative, allow_prefix=True) if is_code else _read(root_fd, relative)
                declarations = _entrypoint_declarations(value, relative)
                targets = _entrypoint_targets(declarations, relative, paths)
                if any(not _entrypoint_targets([declaration], relative, paths) for declaration in declarations):
                    limitations.add('Some declared entrypoint modules were unavailable within the safe bounded repository paths.')
                for order, target in enumerate(targets):
                    entrypoint_paths.add(target)
                    priorities[target] = (-1, order)
                    if target not in scanned and target not in pending:
                        pending.append(target)
                declared_excerpt = '\n'.join(f'Declared entrypoint {command} = {target}' for command, target in declarations)
                if is_code:
                    # Metadata already participates in the cache key; a large file
                    # contributes only a bounded prefix, never an implied full scan.
                    marker = dict(markers).get(relative)
                    if isinstance(marker, list) and marker[3] > _MAX_BYTES:
                        limitations.add('Some source files were limited to a 64 KiB prefix; later implementation may be omitted.')
                    linked = _import_targets(value, relative, code_paths)
                    for order, target in enumerate(linked):
                        if target not in scanned:
                            candidate_priority = (priorities.get(relative, (99, 0))[0] + 1, order)
                            priorities[target] = min(priorities.get(target, candidate_priority), candidate_priority)
                            if target not in pending:
                                pending.append(target)
                files_read += 1
                source_id = 'repository:file:' + hashlib.sha256(relative.encode()).hexdigest()[:24]
                includes = []
                if _manifest(relative) or relative in requirement_paths:
                    before = len(dependencies)
                    if relative in requirement_paths:
                        _requirements(value, relative, source_id, dependencies, includes, limitations)
                    else:
                        _parse_manifest(value, relative, source_id, dependencies, includes, limitations)
                    # Preserve safe normalized declarations, not package-manager credentials/URLs.
                    excerpt = '\n'.join(f"{d['name']} {d['constraint']} ({d['category']})" for d in dependencies[before:])
                    excerpt = '\n'.join(part for part in (declared_excerpt, excerpt) if part)
                    provenance = 'dependency manifest'
                    for include in includes:
                        if include in paths and _safe_relative(include, patterns) and include not in scanned:
                            requirement_paths.add(include)
                            pending.insert(0, include)
                        else:
                            limitations.add('Some requirement includes were excluded or outside the bounded repository paths.')
                elif is_code:
                    excerpt = '\n'.join(part for part in (declared_excerpt, _structural_excerpt(value, relative)) if part)
                    provenance = 'observed in code'
                else:
                    excerpt, omitted = _documentation_excerpt(value)
                    if omitted:
                        limitations.add('Some documentation sections were omitted from bounded excerpts; omitted text is not evidence of absence.')
                    provenance = 'project documentation'
                if excerpt:
                    if len(excerpt) > 3200:
                        limitations.add('Some normalized manifest declaration excerpts were truncated.')
                    sources.append(_source(source_id, relative, excerpt[:3200], provenance, path=relative,
                                           revision=hashlib.sha256(value.encode()).hexdigest()))
            except OSError:
                freshness_unknown = True
                limitations.add('Some selected files could not be read safely; their freshness is unknown.')
            except (ValueError, TypeError, AttributeError, RecursionError):
                limitations.add('Some selected files were unreadable, unsafe, malformed, binary, above 64 KiB, or too deeply nested.')
        if pending:
            limitations.add('The selected file limit was reached; some inputs were omitted.')
        if len(dependencies) > _MAX_DEPENDENCIES:
            dependencies = dependencies[:_MAX_DEPENDENCIES]
            limitations.add('Only the first 100 direct dependency declarations are included.')
        # Prevent an unusually large manifest/dependency set from exhausting the AI input budget.
        bounded, size = [], 0
        repository_sources = sorted(sources[len(records):], key=lambda source: (
            source.get('path') not in entrypoint_paths,
            PurePosixPath(source.get('path', '')).name not in {'setup.py', 'pyproject.toml'}))
        # Reserve half for repository evidence when both kinds are present. Keep
        # whole excerpts and return any unused repository allowance to records.
        repository_reserve = min(_MAX_SOURCES_CHARS // 2, sum(len(s['text']) for s in repository_sources))
        deferred = []
        for source in records:
            if size + len(source['text']) <= _MAX_SOURCES_CHARS - repository_reserve:
                bounded.append(source)
                size += len(source['text'])
            else:
                deferred.append(source)
        for source in [*repository_sources, *deferred]:
            if size + len(source['text']) > _MAX_SOURCES_CHARS:
                limitations.add('The source excerpt budget was reached; some evidence was omitted.')
                continue
            bounded.append(source)
            size += len(source['text'])
        allowed_ids = {source['id'] for source in bounded}
        dependencies = [d for d in dependencies if set(d['source_ids']) <= allowed_ids]
        excerpt_count = sum(source['provenance'] == 'Codex conversation' for source in bounded)
        tree = {}
        for relative in paths:
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
        result = {'sources': bounded, 'dependencies': dependencies,
            'tree': [{'path': path, 'type': kind} for path, kind in sorted(tree.items())],
            'coverage': {'partial': bool(limitations), 'freshness_unknown': freshness_unknown,
                         'limitations': sorted(limitations),
                         'files_inspected': files_read, 'conversation_excerpts': excerpt_count},
            'fingerprint': _digest({'sources': bounded, 'dependencies': dependencies, 'tree': sorted(tree.items()),
                                    'coverage': sorted(limitations), 'sessions': session_markers}),
            'diagnostics': {'cache_hits': 0, 'input_chars': size, 'files_inspected': files_read,
                            'conversation_excerpts': excerpt_count,
                            'duration_ms': round((time.monotonic() - started) * 1000)}}
        with Session(engine) as db:
            if db.get(ProjectHistoryBatch, (project_id, key)) is None:
                db.add(ProjectHistoryBatch(project_id=project_id, fingerprint=key, evidence=[result]))
                db.commit()
        return result
    finally:
        if root_fd is not None:
            os.close(root_fd)
