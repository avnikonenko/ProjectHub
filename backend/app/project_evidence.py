"""Bounded local project evidence; this module never invokes AI or project code."""
from collections import Counter, OrderedDict
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import stat
import subprocess
import threading
import time

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import CodexSession, Note, Project, Task, utcnow
from .services import require_project
from .integrations.codex.knowledge_sources import CONVERSATION_NOTE_SQL

_GIT_TIMEOUT = 5
_GIT_MAX_OUTPUT = 1024 * 1024
_MAX_CHANGED_FILES = 128
_MAX_HASH_BYTES = 8 * 1024 * 1024
_MAX_FILE_BYTES = 512 * 1024
_MAX_PATHS = 300
_DEFAULT_EXCLUSIONS = (
    '.git', '.env', '.env.*', '.aws', '.ssh', '.gnupg', '.codex', 'auth.json',
    '*credentials*', '*secret*', '*.pem', '*.key', '*.p12', '*.pfx', '.npmrc',
    '.pypirc', '.netrc', 'config.toml', '*.local.*', 'node_modules', '.venv', '__pycache__', 'dist', 'build',
)
_SHARE = re.compile(r'https://chatgpt\.com/share/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', re.I)
_DOC_NAMES = {'readme', 'readme.md', 'readme.rst', 'readme.txt', 'contributing.md', 'architecture.md'}
_DEPENDENCIES = {
    'pyproject.toml', 'requirements.txt', 'requirements-dev.txt', 'environment.yml',
    'environment.yaml', 'package.json', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
    'cargo.toml', 'cargo.lock', 'go.mod', 'go.sum', 'pom.xml', 'build.gradle', 'renv.lock',
}
_TEST_CONFIGS = {
    'pytest.ini', 'tox.ini', '.coveragerc', 'vitest.config.ts', 'vitest.config.js',
    'playwright.config.ts', 'playwright.config.js', 'jest.config.js', 'jest.config.ts',
    'conftest.py', 'ctestconfig.cmake',
}
_STOP_WORDS = set('a an and are as at be by can do for from has have how in is it of on or project should that the this to was we what which why with'.split())
_history_cache = OrderedDict()
_history_cache_lock = threading.Lock()


class EvidenceReadError(ValueError):
    """Safe diagnostic which contains no repository contents or raw stderr."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _exclusions():
    raw = os.environ.get('PROJECTHUB_EVIDENCE_EXCLUDE', '')
    if not raw:
        return _DEFAULT_EXCLUSIONS
    if len(raw) > 8000:
        raise EvidenceReadError('Repository evidence exclusions are too large.')
    try:
        configured = json.loads(raw) if raw.lstrip().startswith('[') else raw.split(os.pathsep)
    except ValueError as exc:
        raise EvidenceReadError('Repository evidence exclusions must be a JSON list or path-separated globs.') from exc
    if (not isinstance(configured, list) or len(configured) > 64
            or any(not isinstance(item, str) or not item or len(item) > 200 for item in configured)):
        raise EvidenceReadError('Repository evidence exclusions must contain at most 64 nonempty glob patterns.')
    return (*_DEFAULT_EXCLUSIONS, *configured)


def _excluded(path, patterns):
    parts = PurePosixPath(path).parts
    candidates = [PurePosixPath(*parts[:number]).as_posix() for number in range(1, len(parts) + 1)]
    return any(fnmatch.fnmatchcase(candidate.lower(), pattern.rstrip('/').lower())
               or fnmatch.fnmatchcase(PurePosixPath(candidate).name.lower(), pattern.rstrip('/').lower())
               for candidate in candidates for pattern in patterns)


def _git_environment():
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1', GIT_ALLOW_PROTOCOL='')
    return env


def _git(path, *args, allowed=(0,), overrides=(), limit=_GIT_MAX_OUTPUT, deadline=None):
    """Read at most limit bytes from Git, with a deadline and no arbitrary shell."""
    deadline = deadline if deadline is not None else time.monotonic() + _GIT_TIMEOUT
    if time.monotonic() >= deadline:
        raise EvidenceReadError('Git evidence inspection timed out.')
    command = ['git', '--no-pager', '-C', str(path), '-c', 'core.fsmonitor=false',
               '-c', 'core.hooksPath=/dev/null', *overrides, *args]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   stdin=subprocess.DEVNULL, env=_git_environment(), bufsize=0)
    except OSError as exc:
        raise EvidenceReadError('Git evidence is unavailable.') from exc
    output = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EvidenceReadError('Git evidence inspection timed out.')
                if not selector.select(min(remaining, 0.1)):
                    continue
                chunk = os.read(process.stdout.fileno(), min(65536, limit + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > limit:
                    raise EvidenceReadError('Git evidence exceeded the output limit.')
            try:
                code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise EvidenceReadError('Git evidence inspection timed out.') from exc
        if code not in allowed:
            raise EvidenceReadError('Git evidence could not be read. Check the linked repository.')
        return bytes(output), code
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if process.stdout is not None:
            process.stdout.close()


def _repository_root(value, deadline):
    path = Path(value).expanduser()
    if (not path.is_absolute() or '..' in path.parts or str(path) == '/'
            or any(ord(char) < 32 for char in str(path))
            or any(parent.is_symlink() for parent in (path, *path.parents))
            or not path.is_dir()):
        raise EvidenceReadError('The linked repository path is missing or unsafe.')
    root, _ = _git(path, 'rev-parse', '--show-toplevel', limit=8192, deadline=deadline)
    if Path(os.fsdecode(root).strip()).resolve() != path.resolve():
        raise EvidenceReadError('The linked path is not the repository root.')
    return path


def _status_paths(data):
    modified, untracked, statuses = [], [], []
    entries = iter(data.split(b'\0'))
    for entry in entries:
        if not entry:
            continue
        if len(entry) < 4 or entry[2:3] != b' ':
            raise EvidenceReadError('Git returned unsupported working-tree status.')
        code, path = entry[:2], os.fsdecode(entry[3:])
        statuses.append((path, code.decode('ascii')))
        if code == b'??':
            untracked.append(path)
        else:
            modified.append(path)
        if b'R' in code or b'C' in code:
            old = next(entries, None)
            if old is None:
                raise EvidenceReadError('Git returned incomplete rename information.')
            old_path = os.fsdecode(old)
            modified.append(old_path)
            statuses.append((old_path, code.decode('ascii')))
    return sorted(set(modified)), sorted(set(untracked)), sorted(statuses)


def _file_marker(root_fd, relative, budget):
    """Hash only safe visible changed files, without following descendant links."""
    parts = PurePosixPath(relative).parts
    if not parts or any(part in ('..', '/', '') for part in parts):
        return {'unreadable': True}, 0
    descriptors = []
    try:
        parent = root_fd
        for component in parts[:-1]:
            parent = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            descriptors.append(parent)
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        descriptors.append(descriptor)
        metadata = os.fstat(descriptor)
        marker = {'size': metadata.st_size, 'mtime_ns': metadata.st_mtime_ns,
                  'ctime_ns': metadata.st_ctime_ns, 'mode': metadata.st_mode}
        if not stat.S_ISREG(metadata.st_mode):
            marker['unreadable'] = True
            return marker, 0
        maximum = min(metadata.st_size, _MAX_FILE_BYTES, budget)
        digest = hashlib.sha256()
        count = 0
        while count < maximum:
            chunk = os.read(descriptor, min(65536, maximum - count))
            if not chunk:
                break
            digest.update(chunk)
            count += len(chunk)
        marker['sha256'] = digest.hexdigest()
        marker['limited'] = count < metadata.st_size
        return marker, count
    except FileNotFoundError:
        # Deleted files and rename origins are complete observations, not read
        # failures. A later reappearance changes both status and this marker.
        return {'missing': True}, 0
    except OSError:
        return {'unreadable': True}, 0
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)



_CODE_SUFFIXES = {'.py', '.ts', '.tsx', '.js', '.jsx', '.go', '.rs', '.java', '.kt', '.c', '.h', '.cpp', '.cs', '.r', '.jl', '.vue', '.svelte'}
_CODE_PRIVATE = ('*auth*', '*token*', '*password*', '*config*', '*settings*', '*.lock', '*lock.json', 'vendor', 'generated', '*.min.js')
_CODE_FILE_LIMIT = 12
_CODE_CHAR_LIMIT = 24_000
_CODE_FILE_CHARS = 2000


def _code_category(relative):
    path = PurePosixPath(relative)
    name = path.name.lower()
    if name in _DOC_NAMES or name == 'architecture.md':
        return 'docs'
    if name in _DEPENDENCIES and not name.endswith('.lock') and 'lock' not in name:
        return 'manifest'
    if path.suffix.lower() not in _CODE_SUFFIXES:
        return None
    if name.startswith(('test_', 'test-')) or 'tests' in path.parts or 'test' in path.parts or '.test.' in name or '.spec.' in name:
        return 'tests'
    if name.startswith('__init__.'):
        return None
    return 'code'


def _code_order(relative):
    path = PurePosixPath(relative)
    entrypoints = {'main', 'app', 'index', 'server', 'cli', 'models', 'services', 'router', 'routes'}
    return (path.stem.lower() not in entrypoints, len(path.parts), relative.casefold(), relative)


_DOCUMENTED_PYTHON = re.compile(r'(?<![\w./-])python(?:3(?:\.\d+)?)?\s+((?:\./)?[\w./-]+\.py)(?![\w.-])')
_PYTHON_IMPORT_LINE = re.compile(r'^[ \t]*from[ \t]+([.\w]+)[ \t]+import[ \t]+([^\n#]*)|^[ \t]*import[ \t]+([^\n#]*)', re.M)
_PYTHON_ATTR_CALL = re.compile(r'\b([a-zA-Z_]\w*)\s*\.\s*[a-zA-Z_]\w*\s*\(')


def _code_hints(relative, decoded, available):
    """Find documented commands and local imports in already inspected text."""
    if _code_category(relative) == 'docs':
        parent = PurePosixPath(relative).parent
        names = (match[1].removeprefix('./') for match in _DOCUMENTED_PYTHON.finditer(decoded))
        return list(dict.fromkeys(candidate for name in names for candidate in (name, (parent / name).as_posix())
                                  if candidate in available)), set()
    path = PurePosixPath(relative)
    if path.suffix.lower() != '.py':
        return [], set()

    imports_by_binding = set()
    for match in _PYTHON_IMPORT_LINE.finditer(decoded):
        module, names, imports = match.groups()
        if imports is not None:
            references = [(name, binding or name.split('.')[0]) for name, binding in re.findall(
                r'(?:^|,)\s*(\w+(?:\.\w+)*)(?:\s+as\s+(\w+))?', imports)]
        else:
            base = module.strip('.')
            references = [(base, base.split('.')[0])] if base else []
            references.extend(('.'.join(filter(None, (base, name))), binding or name)
                              for name, binding in re.findall(r'(?:^|[,(])\s*(\w+)(?:\s+as\s+(\w+))?', names))
        for name, binding in references:
            dependency = PurePosixPath(*name.split('.')).with_suffix('.py')
            if module and module.startswith('.'):
                parents = len(module) - len(module.lstrip('.'))
                roots = (path.parents[parents - 1],) if parents <= len(path.parents) else ()
            else:
                roots = (path.parent, PurePosixPath('.'))
            for root in roots:
                candidate = (root / dependency).as_posix()
                if candidate in available and candidate != relative:
                    imports_by_binding.add((binding, candidate))
                    break
    # Prefer modules used by the inspected implementation over incidental imports.
    usage = Counter(re.findall(r'\b([a-zA-Z_]\w*)\s*\.', decoded)) if imports_by_binding else {}
    ranking = Counter()
    for binding, candidate in imports_by_binding:
        ranking[candidate] += usage.get(binding, 0)
    bindings = {binding for binding, _ in imports_by_binding}
    calls, flow_lines = Counter(), set()
    if bindings:
        for index, line in enumerate(decoded.splitlines()):
            if line.lstrip().startswith(('#', 'import ', 'from ')):
                continue
            match = next((match for match in _PYTHON_ATTR_CALL.finditer(line)
                          if match[1] in bindings and calls[match[1]] < 2), None)
            if match:
                calls[match[1]] += 1
                flow_lines.add(index)
            if len(flow_lines) == 8:
                break
    return sorted(ranking, key=lambda name: (-ranking[name], _code_order(name))), flow_lines


def _code_excerpt(root_fd, relative, maximum, deadline, structural=False, measurements=None,
                  code_paths=None, hints=None):
    parts = PurePosixPath(relative).parts
    if not parts or any(part in ('..', '/', '') for part in parts):
        raise OSError('Unsafe relative path')
    opened = []
    try:
        parent = root_fd
        for part in parts[:-1]:
            parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            opened.append(parent)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        opened.append(fd)
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError('Not a regular file')
        data = bytearray()
        maximum_bytes = 65536 if structural else maximum * 4
        while len(data) < maximum_bytes:
            if time.monotonic() >= deadline:
                raise EvidenceReadError('Repository code inspection timed out.')
            chunk = os.read(fd, min(4096, maximum_bytes - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if b'\0' in data:
            raise OSError('Binary file')
        # Decode strictly so a binary payload is not silently made into text.
        # A bounded UTF-8 prefix can end inside a multibyte character.
        decoding_trimmed = False
        try:
            decoded = bytes(data).decode('utf-8')
        except UnicodeDecodeError as exc:
            if exc.end != len(data) or exc.reason != 'unexpected end of data':
                raise OSError('Non-UTF-8 file') from exc
            decoded = bytes(data[:exc.start]).decode('utf-8')
            decoding_trimmed = True
        reasons = ['byte_limit'] if len(data) < metadata.st_size else []
        if decoding_trimmed:
            reasons.append('utf8_boundary')
        targets, flow_lines = _code_hints(relative, decoded, code_paths) if code_paths is not None else ([], set())

        def finish(excerpt, omissions):
            # The initial size is only meaningful for these bytes if the file
            # stayed unchanged while being read and its excerpt was prepared.
            # A growing/shrinking file can otherwise report impossible byte
            # counts or a complete excerpt assembled from different revisions.
            current = os.fstat(fd)
            identity = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
            if any(getattr(current, key) != getattr(metadata, key) for key in identity):
                raise OSError('Repository file changed during inspection')
            if measurements is not None:
                measurements.update(path=relative, file_bytes=metadata.st_size,
                                    bytes_read=len(data), excerpt_chars=len(excerpt),
                                    local_read_complete=len(data) == metadata.st_size,
                                    excerpt_complete=not omissions,
                                    omission_reasons=omissions)
            if hints is not None:
                for target in targets:
                    hints.setdefault(target, set()).add(relative)
            return excerpt, bool(omissions)

        if not structural:
            if len(decoded) > maximum:
                reasons.append('character_limit')
            return finish(decoded[:maximum], reasons)
        # Read a bounded source prefix, then expose declarations and routes
        # instead of letting imports consume the complete output allowance.
        lines = decoded.splitlines()
        signature = re.compile(
            r'^\s*(?:(?:async\s+)?def\s+|class\s+|'
            r'@[\w.]+\.(?:get|post|put|patch|delete)\s*\(|'
            r'(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+|'
            r'(?:export\s+)?(?:const|let)\s+(?:tabs|routes|router)\s*=)')
        selected = set(range(min(6, len(lines))))
        for number, line in enumerate(lines):
            if signature.match(line):
                selected.add(number)
                # Multiline navigation declarations need their item labels.
                if re.search(r'\b(?:tabs|routes)\s*=', line):
                    selected.update(range(number, min(number + 10, len(lines))))
        # Reserve a bounded share for actual calls to locally resolved modules.
        # Keep these original source lines in chronological order with signatures.
        rendered, used = {}, 0
        for candidates, ceiling in ((flow_lines - selected, maximum // 3), (selected, maximum)):
            for number in sorted(candidates):
                line = f'L{number + 1}: {lines[number][:200]}'
                if used + len(line) + 1 > ceiling:
                    continue
                rendered[number] = line
                used += len(line) + 1
        excerpt = '\n'.join(rendered[number] for number in sorted(rendered))
        if len(selected) < len(lines):
            reasons.append('declaration_selection')
        if (selected | flow_lines) - rendered.keys():
            reasons.append('excerpt_limit')
        if any(len(lines[number]) > 200 for number in selected | flow_lines):
            reasons.append('line_width_limit')
        return finish(excerpt, reasons)
    finally:
        for fd in reversed(opened):
            os.close(fd)


def _repository_coverage(status, **changes):
    """Measurements describe bounded tracked-file evidence, never all disk content."""
    return {
        'status': status, 'inventory_scope': 'git_tracked', 'inventory_complete': False,
        'total_files': None, 'eligible_files': None, 'read_files': 0, 'ai_input_files': 0,
        'full_files': 0, 'partial_files': 0, 'excluded_files': None,
        'unsupported_files': None, 'not_selected_files': None, 'failed_files': 0,
        'empty_files': 0, 'paths': {key: [] for key in ('read', 'partial', 'not_selected', 'failed', 'empty')},
        'paths_truncated': False, 'by_category': {}, 'file_details': [],
        'limits': {'max_files': _CODE_FILE_LIMIT, 'max_chars': _CODE_CHAR_LIMIT,
                   'max_file_chars': _CODE_FILE_CHARS},
        'notes': ['Inventory covers Git-tracked paths only; untracked files (including ignored untracked files) and submodule contents are not inspected.',
                  'Code/test excerpts are evidence of source content, not proof that features work or tests pass.'],
        **changes,
    }


def collect_repository_code(repository_path):
    """Read small tracked-file excerpts only for an explicitly requested analysis."""
    if not repository_path:
        return {'sources': [], 'coverage': ['No repository is linked; no code excerpts were read.'],
                'inspection': _repository_coverage('not_linked')}
    sources, skipped, limited = [], 0, False
    inspection = _repository_coverage('unavailable')
    try:
        deadline = time.monotonic() + _GIT_TIMEOUT
        path = _repository_root(repository_path, deadline)
        patterns = (*_exclusions(), *_CODE_PRIVATE)
        listed, _ = _git(path, 'ls-files', '-z', '--cached', deadline=deadline)
        all_paths = sorted({os.fsdecode(item) for item in listed.split(b'\0') if item})
        groups = {kind: [] for kind in ('docs', 'manifest', 'code', 'tests')}
        excluded, unsupported = 0, 0
        for relative in all_paths:
            if _excluded(relative, patterns) or any(ord(char) < 32 for char in relative):
                excluded += 1
                continue
            category = _code_category(relative)
            if category:
                groups[category].append(relative)
            else:
                unsupported += 1
        eligible = {name for files in groups.values() for name in files}
        inspection.update(inventory_complete=True, total_files=len(all_paths),
                          eligible_files=len(eligible), excluded_files=excluded,
                          unsupported_files=unsupported)
        for files in groups.values():
            files.sort(key=_code_order)
        groups['docs'].sort(key=lambda name: (
            0 if PurePosixPath(name).name.lower().startswith('readme') else
            1 if PurePosixPath(name).name.lower() == 'architecture.md' else 2,
            len(PurePosixPath(name).parts), name.casefold(), name))
        buckets = OrderedDict()
        for name in groups['code']:
            parts = PurePosixPath(name).parts
            buckets.setdefault(parts[0] if len(parts) > 1 else '.', []).append(name)
        preferred = {name: files for name, files in buckets.items()
                     if name.lower() in ('.', 'backend', 'frontend', 'src', 'app', 'lib')}
        if preferred:
            # Development tooling and migration files should not displace the
            # application modules that explain runtime responsibilities.
            buckets = preferred
        code = []
        while any(buckets.values()) and len(code) < 7:
            for files in buckets.values():
                if files:
                    code.append(files.pop(0))
                    if len(code) == 7:
                        break
        code_paths = set(groups['code'])
        hints = {}

        def candidates():
            yield from groups['docs'][:2]
            yield from groups['manifest'][:1]
            chosen = set()
            for _ in range(7):
                target = next((name for name in (*hints, *code) if name not in chosen), None)
                if target is None:
                    break
                chosen.add(target)
                yield target
            yield from groups['tests'][:2]

        attempted, partial, failed, empty, file_details = set(), [], [], [], []
        root_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            remaining = _CODE_CHAR_LIMIT
            for index, relative in enumerate(candidates()):
                if index == _CODE_FILE_LIMIT:
                    break
                header = f'Repository file: {relative}\nObserved source excerpt; this does not prove functionality or test success.\n'
                maximum = min(_CODE_FILE_CHARS, remaining) - len(header)
                if maximum <= 0:
                    limited = True
                    break
                attempted.add(relative)
                measured_file = {}
                try:
                    excerpt, clipped = _code_excerpt(root_fd, relative, maximum, deadline,
                                                      structural=_code_category(relative) in ('code', 'tests'),
                                                      measurements=measured_file, code_paths=code_paths, hints=hints)
                except OSError:
                    skipped += 1
                    failed.append(relative)
                    continue
                if not excerpt.strip():
                    empty.append(relative)
                    continue
                limited |= clipped
                if clipped:
                    partial.append(relative)
                value = header + excerpt
                sources.append({'id': f'repository:code:{relative}', 'label': relative, 'text': value,
                                'classification': 'verified', 'path': relative})
                file_details.append(measured_file)
                remaining -= len(value)
        finally:
            os.close(root_fd)
        for source in sources:
            origins = hints.get(source['path'])
            if origins:
                source['repository_role'] = ('documented_python_entrypoint' if any(
                    _code_category(origin) == 'docs' for origin in origins) else 'referenced_python_module')
                source['referenced_by'] = sorted(origins)
        limited |= len(eligible) > len(attempted)
        path_groups = {'read': [source['path'] for source in sources], 'partial': partial,
                       'not_selected': sorted(eligible - attempted), 'failed': failed, 'empty': empty}
        inspection.update(status='available', read_files=len(sources), ai_input_files=len(sources),
                          full_files=len(sources) - len(partial), partial_files=len(partial),
                          not_selected_files=len(eligible - attempted), failed_files=len(failed),
                          empty_files=len(empty), paths={key: values[:100] for key, values in path_groups.items()},
                          paths_truncated=any(len(values) > 100 for values in path_groups.values()),
                          file_details=file_details[:100])
        inspection['by_category'] = {
            kind: {'eligible_files': len(files),
                   'read_files': sum(source['path'] in files for source in sources),
                   'full_files': sum(source['path'] in files and source['path'] not in partial for source in sources),
                   'partial_files': sum(name in files for name in partial)}
            for kind, files in groups.items()
        }
        inspection['notes'].append('Eligible files are supported documentation, dependency manifests, source code and tests after configured/private-file exclusions.')
        if limited:
            inspection['notes'].append('Representative selection and excerpt limits restrict this inspection; see the unselected-file and partial-read counts. This is not a complete code review.')
        if failed:
            inspection['notes'].append('Some selected files could not be used: unreadable, symlinked, binary, non-UTF-8 or changed during inspection.')
    except (EvidenceReadError, OSError) as exc:
        diagnostic = str(exc) if isinstance(exc, EvidenceReadError) else 'Repository code is unavailable.'
        # A failed collection returns no excerpts. Never claim partial local reads
        # were supplied to synthesis or expose private excluded filenames.
        inspection = _repository_coverage('unavailable', notes=[diagnostic])
        return {'sources': [], 'coverage': [diagnostic + ' No code evidence was included.'],
                'inspection': inspection}
    coverage = [f'Read {len(sources)} tracked repository file excerpts, bounded to {_CODE_FILE_LIMIT} files '
                f'and {_CODE_CHAR_LIMIT:,} characters; no project code or tests were executed.',
                'Repository excerpts are untrusted source text, not proof of functioning features or successful tests.']
    if limited:
        coverage.append('Code coverage is partial: representative files, bounded prefixes, and selected source declarations only.')
    if skipped:
        coverage.append(f'Skipped {skipped} unreadable, symlinked, or binary repository files.')
    return {'sources': sources, 'coverage': coverage, 'inspection': inspection}

def _git_evidence(repository_path, patterns):
    deadline = time.monotonic() + _GIT_TIMEOUT
    path = _repository_root(repository_path, deadline)

    def git(*args, **kwargs):
        return _git(path, *args, **kwargs, deadline=deadline)

    keys, _ = git('config', '--null', '--name-only', '--get-regexp',
                   r'^filter\..*\.(clean|process|required)$', allowed=(0, 1), limit=65536)
    drivers = sorted({os.fsdecode(key).rsplit('.', 1)[0] for key in keys.split(b'\0') if key})
    if any('=' in driver or any(ord(c) < 32 for c in driver) for driver in drivers):
        raise EvidenceReadError('Git filter configuration cannot be inspected safely.')
    overrides = [item for driver in drivers for option in ('clean=', 'process=', 'required=false')
                 for item in ('-c', f'{driver}.{option}')]
    branch, branch_code = git('symbolic-ref', '--quiet', '--short', 'HEAD', allowed=(0, 1), limit=8192)
    head, head_code = git('rev-parse', '--quiet', '--verify', 'HEAD', allowed=(0, 1), limit=8192)
    commits = []
    if head_code == 0:
        log, _ = git('log', '-3', '--no-show-signature', '--format=%H%x00%s%x00', 'HEAD', '--', limit=65536)
        fields = log.split(b'\0')
        for number in range(0, len(fields) - 1, 2):
            commit, subject = fields[number].strip(), fields[number + 1]
            if commit:
                commits.append({'hash': commit.decode('ascii'), 'subject': subject.decode('utf-8', 'replace')[:240]})
    status, _ = git('status', '--porcelain=v1', '-z', '--untracked-files=all',
                     '--ignore-submodules=all', overrides=overrides)
    all_modified, all_untracked, all_statuses = _status_paths(status)
    visible_statuses = [item for item in all_statuses if not _excluded(item[0], patterns)]
    modified = [name for name in all_modified if not _excluded(name, patterns)]
    untracked = [name for name in all_untracked if not _excluded(name, patterns)]
    listed, _ = git('ls-files', '-z', '--cached', '--others', '--exclude-standard', overrides=overrides)
    staged, _ = git('ls-files', '-z', '--stage', overrides=overrides)
    index_entries = []
    for entry in staged.split(b'\0'):
        if not entry:
            continue
        metadata, separator, name = entry.partition(b'\t')
        if not separator:
            raise EvidenceReadError('Git returned unsupported index information.')
        relative = os.fsdecode(name)
        if not _excluded(relative, patterns):
            index_entries.append((relative, metadata.decode('ascii')))
    files = sorted({os.fsdecode(name) for name in listed.split(b'\0')
                    if name and not _excluded(os.fsdecode(name), patterns)})
    docs = [name for name in files if PurePosixPath(name).name.lower() in _DOC_NAMES
            or (PurePosixPath(name).parts[0].lower() in ('docs', 'documentation')
                and PurePosixPath(name).suffix.lower() in ('.md', '.rst', '.txt'))]
    test_configs = [name for name in files if PurePosixPath(name).name.lower() in _TEST_CONFIGS
                    or PurePosixPath(name).name.lower().startswith(('test_', 'test-'))
                    or '/tests/' in '/' + name]
    manifests = [name for name in files if PurePosixPath(name).name.lower() in _DEPENDENCIES]
    markers = {}
    budget = _MAX_HASH_BYTES
    root_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in sorted(set(modified + untracked))[:_MAX_CHANGED_FILES]:
            if time.monotonic() >= deadline:
                raise EvidenceReadError('Git evidence inspection timed out.')
            marker, consumed = _file_marker(root_fd, name, budget)
            markers[name] = marker
            budget -= consumed
    finally:
        os.close(root_fd)
    change_detection_limited = (len(markers) < len(set(modified + untracked))
                                or any(marker.get('limited') or marker.get('unreadable')
                                       for marker in markers.values()))
    result = {
        'branch': branch.decode('utf-8', 'replace').strip() if branch_code == 0 else None,
        'head': head.decode('ascii').strip() if head_code == 0 else None,
        'dirty': bool(status),
        'commits': commits,
        'modified': modified[:_MAX_PATHS], 'untracked': untracked[:_MAX_PATHS],
        'structure': files[:_MAX_PATHS], 'docs': docs[:30], 'test_configs': test_configs[:30],
        'dependency_manifests': manifests[:30],
        'change_detection_limited': change_detection_limited,
        'limited': len(files) > _MAX_PATHS or len(modified) > _MAX_PATHS or len(untracked) > _MAX_PATHS
                   or change_detection_limited,
        'coverage': 'Filenames and Git metadata only; no source code or test results read. '
                    'Ignored files, private-file patterns, configured exclusions, and submodule contents are omitted.',
    }
    fingerprint = _digest({'git': result, 'paths': {'modified': modified, 'untracked': untracked, 'files': files},
                           'statuses': visible_statuses, 'index': index_entries,
                           'changed_file_markers': markers, 'exclusions': patterns})
    return result, fingerprint


def _blob_digest(db, table, column, row_id, digest):
    """Use SQLite's incremental BLOB reader to avoid allocating long transcripts."""
    connection = db.connection().connection.driver_connection
    with connection.blobopen(table, column, row_id, readonly=True) as blob:
        while chunk := blob.read(65536):
            digest.update(chunk)


def _history_digest(db, project_id, identity):
    state = db.execute(text('SELECT indexed_at FROM codex_history_index WHERE project_id=:pid'),
                       {'pid': project_id}).scalar_one_or_none()
    database = db.get_bind().url.database
    key = (db.get_bind(), database, project_id)
    marker = _digest([state, identity])
    with _history_cache_lock:
        cached = _history_cache.get(key)
        if cached and cached[0] == marker:
            _history_cache.move_to_end(key)
            return cached[1], state
    digest = hashlib.sha256()
    rows = db.execute(text('SELECT h.id,h.session_id,h.turn_id,h.status FROM codex_history_turns h '
        'JOIN codex_sessions s ON s.id=h.session_id AND s.project_id=h.project_id '
        'WHERE h.project_id=:pid AND s.is_linked=1 ORDER BY h.session_id,h.turn_id'), {'pid': project_id})
    for row in rows:
        digest.update(_digest([row.session_id, row.turn_id, row.status]).encode())
        _blob_digest(db, 'codex_history_turns', 'content', row.id, digest)
        digest.update(b'\0')
    value = digest.hexdigest()
    with _history_cache_lock:
        _history_cache[key] = (marker, value)
        _history_cache.move_to_end(key)
        while len(_history_cache) > 32:
            _history_cache.popitem(last=False)
    return value, state


def _reported_sources(db, project_id, objective):
    from .integrations.codex.history_duplicates import deduplicate_sources
    sources = []
    recent = db.execute(text(
        'SELECT h.session_id,h.turn_id,s.title,h.status,length(h.content) AS chars,'
        'substr(h.content,1,400) AS first,substr(h.content,-700) AS last '
        'FROM codex_history_turns h JOIN codex_sessions s ON s.id=h.session_id AND s.project_id=h.project_id '
        'WHERE h.project_id=:pid AND s.is_linked=1 '
        'ORDER BY coalesce(s.codex_activity_at,s.created_at) DESC,h.id DESC LIMIT 10'), {'pid': project_id}).mappings()
    seen = set()
    for row in recent:
        source_id = f"session:{row['session_id']}:turn:{row['turn_id']}"
        excerpt = row['first'] + '\n…\n' + row['last']
        # For a short turn fetch just its bounded content to avoid overlap.
        if row['chars'] <= 1100:
            excerpt = db.execute(text('SELECT content FROM codex_history_turns WHERE project_id=:pid AND session_id=:sid AND turn_id=:tid'),
                {'pid': project_id, 'sid': row['session_id'], 'tid': row['turn_id']}).scalar_one()
        sources.append({'id': source_id, 'label': f"{row['title']} · turn {row['turn_id']}",
                        'text': excerpt, 'classification': 'reported',
                        'session_id': row['session_id'], 'turn_id': row['turn_id'], 'recent': True})
        seen.add(source_id)
    terms = list(dict.fromkeys(word for word in re.findall(r'[^\W_]+', objective.lower())
                               if len(word) > 2 and word not in _STOP_WORDS))[:10]
    if terms:
        match = ' OR '.join(f'"{term}"' for term in terms)
        rows = db.execute(text(
            "SELECT h.session_id,h.turn_id,s.title,snippet(codex_history_fts,0,'','','…',60) AS excerpt "
            'FROM codex_history_fts JOIN codex_history_turns h ON h.id=codex_history_fts.rowid '
            'JOIN codex_sessions s ON s.id=h.session_id AND s.project_id=h.project_id '
            'WHERE codex_history_fts MATCH :query AND h.project_id=:pid AND s.is_linked=1 '
            'ORDER BY codex_history_fts.rank,h.session_id,h.turn_id LIMIT 12'),
            {'query': match, 'pid': project_id}).mappings()
        added = 0
        for row in rows:
            source_id = f"session:{row['session_id']}:turn:{row['turn_id']}"
            if source_id in seen:
                continue
            sources.append({'id': source_id, 'label': f"{row['title']} · turn {row['turn_id']}",
                            'text': row['excerpt'][:1100], 'classification': 'reported',
                            'session_id': row['session_id'], 'turn_id': row['turn_id']})
            added += 1
            if added == 2:
                break
    return deduplicate_sources(db, project_id, sources)


def collect_project_evidence(engine, project_id):
    """Collect current deterministic observations and bounded reported excerpts."""
    with Session(engine) as db:
        project = require_project(db, project_id)
        objective = project.goals.strip() or project.description.strip() or project.name
        repository_path = project.repository_path
        identity = [tuple(row) for row in db.execute(select(
            CodexSession.id, CodexSession.thread_id, CodexSession.title, CodexSession.codex_account,
            CodexSession.topic, CodexSession.codex_activity_at, CodexSession.is_linked,
        ).where(CodexSession.project_id == project_id).order_by(CodexSession.id))]
        history_digest, indexed_at = _history_digest(db, project_id, identity)
        sources = [{'id': 'project:metadata', 'label': 'Project objective and scope',
                    'text': (f'{project.name}\n{project.description}\nGoals: {project.goals}\n'
                             f'Project status (user supplied): {project.status}')[:1800],
                    'classification': 'reported'}]
        reported = _reported_sources(db, project_id, objective)
        content_digest = hashlib.sha256()
        for table, fields in (
            ('notes', 'id,title,kind,reference,updated_at'),
            ('tasks', 'id,title,status,priority,updated_at'),
        ):
            projection = fields + f",kind='document' AND {CONVERSATION_NOTE_SQL} AS imported" if table == 'notes' else fields
            for row in db.execute(text(f'SELECT {projection} FROM {table} WHERE project_id=:pid ORDER BY id'), {'pid': project_id}):
                content_digest.update(_digest(list(row[:-1] if table == 'notes' else row)).encode())
                _blob_digest(db, table, 'content' if table == 'notes' else 'description', row.id, content_digest)
                content_digest.update(b'\0')
                if table == 'notes' and row.imported:
                    _blob_digest(db, 'notes', 'conversation', row.id, content_digest)
                    content_digest.update(b'\0')
        # Legacy accepted decisions still feed brief generation. Hash all of
        # their text without loading long fields, but never foreign records.
        for row in db.execute(text("SELECT id,updated_at FROM decisions WHERE project_id=:pid "
                                   "AND status='accepted' ORDER BY id"), {'pid': project_id}):
            content_digest.update(_digest(['decision', row.id, row.updated_at]).encode())
            for field in ('title', 'description', 'rationale', 'evidence', 'limitations', 'next_step'):
                _blob_digest(db, 'decisions', field, row.id, content_digest)
                content_digest.update(b'\0')
        notes = db.execute(text(f'SELECT id,title,kind,reference,{CONVERSATION_NOTE_SQL} AS canonical,substr(content,1,1100) AS content '
                               'FROM notes WHERE project_id=:pid ORDER BY id DESC LIMIT 8'), {'pid': project_id}).mappings()
        for note in notes:
            reported.append({'id': f"note:{note['id']}", 'label': note['title'][:200],
                             'text': (f"{note['kind']}: {note['title']}\n{note['content']}")[:1300],
                             'classification': 'reported', 'note_id': note['id'],
                             'share': bool(note['kind'] == 'document' and note['canonical'] or _SHARE.fullmatch(note['reference']))})
        tasks = db.execute(text('SELECT id,title,status,priority,substr(description,1,900) AS description '
                               "FROM tasks WHERE project_id=:pid AND status!='not_planned' "
                               "ORDER BY status='completed',id DESC LIMIT 6"), {'pid': project_id}).mappings()
        for task in tasks:
            reported.append({'id': f"task:{task['id']}", 'label': f"TODO #{task['id']} · {task['title'][:160]}",
                             'text': f"User-recorded TODO ({task['status']}, {task['priority']}): {task['title'][:500]}\n{task['description']}",
                             'classification': 'reported'})
        project_marker = [project.name, project.description, project.goals, project.status,
                          project.repository_path, project.architecture_mermaid, project.tags]
    git, error, git_fingerprint, checked = None, None, None, None
    if repository_path:
        checked = utcnow()
        try:
            git, git_fingerprint = _git_evidence(repository_path, _exclusions())
        except (EvidenceReadError, OSError) as exc:
            error = str(exc) if isinstance(exc, EvidenceReadError) else 'Git evidence is unavailable.'
        if git is not None:
            sources.append({'id': 'git:state', 'label': 'Observed Git state', 'classification': 'verified',
                            'text': f"Observed branch: {git['branch'] or 'detached HEAD'}; HEAD: {git['head'] or 'no commits'}. "
                                    f"Uncommitted changes present: {git['dirty']}. Modified paths: {', '.join(git['modified'][:20]) or 'none visible'}. "
                                    f"Untracked paths: {', '.join(git['untracked'][:20]) or 'none visible'}. "
                                    'These observations do not establish implemented features or successful tests.'})
            for commit in git['commits']:
                sources.append({'id': f"git:commit:{commit['hash']}", 'label': f"Commit {commit['hash'][:10]}",
                                'classification': 'verified', 'commit': commit['hash'],
                                'text': f"Recorded Git commit {commit['hash']}: {commit['subject']}. "
                                        'The commit message is a reported description, not proof the feature works.'})
            if git['structure']:
                sources.append({'id': 'repository:files', 'label': 'Observed repository filenames',
                                'classification': 'verified',
                                'text': json.dumps({key: git[key] for key in ('docs', 'test_configs', 'dependency_manifests')},
                                                   ensure_ascii=True)[:1800]
                                        + '\nFile existence is verified; implementation correctness and test execution are not.'})
    sources.extend(reported)
    for source in sources:
        source['label'] = source['label'][:240]
        source['text'] = source['text'][:1800]
    return {
        'objective': objective[:1800], 'git': git, 'git_error': error, 'git_checked_at': checked,
        'history_indexed_at': indexed_at,
        'validation': 'No verified test or validation results.',
        'sources': sources,
        'fingerprint': _digest({'project': project_marker, 'history': history_digest, 'sessions': identity,
                                'records': content_digest.hexdigest(), 'git': git_fingerprint, 'git_error': error}),
    }
