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


def _read(root_fd, relative):
    fd = _descriptor(root_fd, relative)
    try:
        data = bytearray()
        while len(data) <= _MAX_BYTES:
            chunk = os.read(fd, min(8192, _MAX_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > _MAX_BYTES or b'\0' in data:
            raise ValueError('Oversized or binary file')
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
    paths = sorted(paths, key=lambda value: (not _manifest(value), *_code_order(value)))[:_MAX_PATHS]
    manifests = sorted((v for v in paths if _manifest(v)), key=_code_order)[:_MAX_MANIFESTS]
    docs = sorted((v for v in paths if _code_category(v) == 'docs'), key=_code_order)[:3]
    code = sorted((v for v in paths if _code_category(v) == 'code'), key=_code_order)
    # Round-robin top-level areas so a backend does not hide a frontend.
    buckets = {}
    for value in code:
        buckets.setdefault(PurePosixPath(value).parts[0] if '/' in value else '.', []).append(value)
    chosen = []
    while any(buckets.values()) and len(chosen) < _MAX_FILES - len(manifests) - len(docs):
        for values in buckets.values():
            if values and len(chosen) < _MAX_FILES - len(manifests) - len(docs):
                chosen.append(values.pop(0))
    candidates = [*manifests, *docs, *chosen]
    if len(candidates) < len(paths):
        limits.append('Only selected source files and bounded declaration excerpts were inspected.')
    return root, paths, candidates, limits


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


def _structural_excerpt(value, relative):
    """Expose declarations and import targets, never initializer/default literals."""
    declarations = []
    if PurePosixPath(relative).suffix.lower() == '.py':
        try:
            parsed = ast.parse(value)
        except (SyntaxError, ValueError, RecursionError):
            parsed = None
        if parsed is not None:
            nodes = sorted(ast.walk(parsed), key=lambda node: getattr(node, 'lineno', 0))
            for node in nodes:
                if isinstance(node, ast.Import):
                    declaration = 'import ' + ', '.join(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    declaration = 'from ' + '.' * node.level + (node.module or '') + ' import ' + ', '.join(alias.name for alias in node.names)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    declaration = 'def ' + node.name + '(...)'
                elif isinstance(node, ast.ClassDef):
                    declaration = 'class ' + node.name
                else:
                    continue
                declarations.append(f'L{node.lineno}: {declaration}')
            return '\n'.join(declarations)[:_MAX_EXCERPT]
    for number, line in enumerate(value.splitlines(), 1):
        # Import syntax may share a line with executable code; extract its module
        # literal only. Everything after a declaration name is deliberately omitted.
        imported = re.search(r"(?:\bfrom\s*|^\s*import\s*)['\"]([^'\"]{1,180})['\"]", line)
        declaration = re.match(r'\s*(?:export\s+(?:default\s+)?)?(?:(?:async\s+)?(?:function|def)\s+|class\s+|(?:const|let|var|interface|type|enum)\s+)([A-Za-z_$][\w$]*)', line)
        if imported:
            target = imported[1]
            if '://' in target or any(char in target for char in ('?', '#')):
                target = '[external module location omitted]'
            declarations.append(f'L{number}: import {target}')
        elif declaration:
            declarations.append(f'L{number}: declaration {declaration[1]}')
    return '\n'.join(declarations)[:_MAX_EXCERPT]


def _records(db, project):
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
    turns = db.execute(text('SELECT h.id,h.session_id,h.turn_id,substr(h.content,1,1600) AS excerpt,length(h.content) AS content_length '
        'FROM codex_history_turns h JOIN codex_sessions s ON s.id=h.session_id AND s.project_id=h.project_id '
        "WHERE h.project_id=:pid AND s.is_linked=1 AND h.status!='inProgress' ORDER BY h.id DESC LIMIT 12"),
        {'pid': project.id}).mappings().all()
    if any(row['content_length'] > 1600 for row in turns):
        limitations.append('Some indexed conversation excerpts were truncated to 1,600 characters.')
    for row in turns:
        sources.append(_source(f"session:{row['session_id']}:turn:{row['turn_id']}",
            f"Session #{row['session_id']}, turn {row['turn_id']}", row['excerpt'], 'Codex conversation',
            session_id=row['session_id'], turn_id=row['turn_id']))
    notes = db.execute(text('SELECT id,substr(title,1,160) AS title,substr(content,1,1200) AS content,kind,updated_at, '
                           'length(content) AS content_length,length(title) AS title_length '
                           'FROM notes WHERE project_id=:pid ORDER BY id DESC LIMIT 7'), {'pid': project.id}).mappings().all()
    if len(notes) > 6:
        limitations.append('Only the six most recent notes or imported documents were included.')
    if any(row['content_length'] > 1200 or row['title_length'] > 160 for row in notes[:6]):
        limitations.append('Some note titles or contents were truncated to bounded excerpts.')
    for row in notes[:6]:
        sources.append(_source(f"note:{row['id']}", row['title'], row['content'],
                               'imported discussion' if row['kind'] == 'document' else 'project note', date=row['updated_at']))
    brief = db.execute(text('SELECT substr(sections,1,1800),generated_at,length(sections) FROM project_briefs WHERE project_id=:pid'),
                       {'pid': project.id}).first()
    if brief:
        if brief[2] > 1800:
            limitations.append('The previous AI summary was truncated to a 1,800-character excerpt.')
        sources.append(_source('brief:previous', 'Previous AI project summary (interpretation)', brief[0],
                               'prior AI interpretation', date=brief[1]))
    return sources, markers, limitations, len(turns)


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
        key = _digest({'namespace': 'scheme-inspection-v2', 'project_id': project_id,
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
        requirement_paths = {path for path in candidates if PurePosixPath(path).name.lower().startswith('requirements')}
        while pending and len(scanned) < _MAX_FILES:
            relative = pending.pop(0)
            if relative in scanned:
                continue
            scanned.append(relative)
            try:
                value = _read(root_fd, relative)
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
                    provenance = 'dependency manifest'
                    for include in includes:
                        if include in paths and _safe_relative(include, patterns) and include not in scanned:
                            requirement_paths.add(include)
                            pending.insert(0, include)
                        else:
                            limitations.add('Some requirement includes were excluded or outside the bounded repository paths.')
                elif _code_category(relative) == 'code':
                    excerpt = _structural_excerpt(value, relative)
                    provenance = 'observed in code'
                else:
                    excerpt = value[:_MAX_EXCERPT]
                    provenance = 'project documentation'
                if excerpt:
                    if len(excerpt) > 3200:
                        limitations.add('Some normalized manifest declaration excerpts were truncated.')
                    sources.append(_source(source_id, relative, excerpt[:3200], provenance, path=relative,
                                           revision=hashlib.sha256(value.encode()).hexdigest()))
            except OSError:
                freshness_unknown = True
                limitations.add('Some selected files could not be read safely; their freshness is unknown.')
            except (ValueError, TypeError, AttributeError):
                limitations.add('Some selected files were unreadable, unsafe, malformed, binary, or above 64 KiB.')
        if pending:
            limitations.add('The selected file limit was reached; some inputs were omitted.')
        if len(dependencies) > _MAX_DEPENDENCIES:
            dependencies = dependencies[:_MAX_DEPENDENCIES]
            limitations.add('Only the first 100 direct dependency declarations are included.')
        # Prevent an unusually large manifest/dependency set from exhausting the AI input budget.
        bounded, size = [], 0
        for source in sources:
            if size + len(source['text']) > _MAX_SOURCES_CHARS:
                limitations.add('The source excerpt budget was reached; some evidence was omitted.')
                continue
            bounded.append(source)
            size += len(source['text'])
        allowed_ids = {source['id'] for source in bounded}
        dependencies = [d for d in dependencies if set(d['source_ids']) <= allowed_ids]
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
