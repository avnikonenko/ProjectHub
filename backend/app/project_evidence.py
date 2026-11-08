"""Bounded local project evidence; this module never invokes AI or project code."""
from collections import OrderedDict
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
    modified, untracked = [], []
    entries = iter(data.split(b'\0'))
    for entry in entries:
        if not entry:
            continue
        if len(entry) < 4 or entry[2:3] != b' ':
            raise EvidenceReadError('Git returned unsupported working-tree status.')
        code, path = entry[:2], os.fsdecode(entry[3:])
        if code == b'??':
            untracked.append(path)
        else:
            modified.append(path)
        if b'R' in code or b'C' in code:
            old = next(entries, None)
            if old is None:
                raise EvidenceReadError('Git returned incomplete rename information.')
            modified.append(os.fsdecode(old))
    return sorted(set(modified)), sorted(set(untracked))


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
    except OSError:
        return {'unreadable': True}, 0
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


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
    all_modified, all_untracked = _status_paths(status)
    modified = [name for name in all_modified if not _excluded(name, patterns)]
    untracked = [name for name in all_untracked if not _excluded(name, patterns)]
    listed, _ = git('ls-files', '-z', '--cached', '--others', '--exclude-standard', overrides=overrides)
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
                        'session_id': row['session_id'], 'turn_id': row['turn_id']})
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
    return sources


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
            for row in db.execute(text(f'SELECT {fields} FROM {table} WHERE project_id=:pid ORDER BY id'), {'pid': project_id}):
                content_digest.update(_digest(list(row)).encode())
                _blob_digest(db, table, 'content' if table == 'notes' else 'description', row.id, content_digest)
                content_digest.update(b'\0')
        notes = db.execute(text('SELECT id,title,kind,reference,substr(content,1,1100) AS content '
                               'FROM notes WHERE project_id=:pid ORDER BY id DESC LIMIT 8'), {'pid': project_id}).mappings()
        for note in notes:
            reported.append({'id': f"note:{note['id']}", 'label': note['title'][:200],
                             'text': (f"{note['kind']}: {note['title']}\n{note['content']}")[:1300],
                             'classification': 'reported', 'note_id': note['id'],
                             'share': bool(_SHARE.fullmatch(note['reference']))})
        tasks = db.execute(text('SELECT id,title,status,priority,substr(description,1,900) AS description '
                               'FROM tasks WHERE project_id=:pid '
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
        'validation': 'No independently recorded test execution or scientific validation result is available. '
                      'Test files and statements in conversations are not evidence of a passing test run.',
        'sources': sources,
        'fingerprint': _digest({'project': project_marker, 'history': history_digest, 'sessions': identity,
                                'records': content_digest.hexdigest(), 'git': git_fingerprint, 'git_error': error}),
    }
