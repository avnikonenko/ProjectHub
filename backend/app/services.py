import os
import re
import subprocess
from types import SimpleNamespace
from pathlib import Path
from urllib.parse import unquote, urlsplit
from fastapi import HTTPException
from sqlalchemy import select, text
from .integrations.github_issues import github_repository, issues_url
from .models import Project, Question, Answer, Decision, Task, Note, AuditEvent, CodexSession, utcnow


def serialize(obj):
    row = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    if isinstance(obj, Project):
        row['git_remote'] = safe_project_git_remote(row['git_remote'])
    elif isinstance(obj, AuditEvent) and row['entity_type'] == 'projects':
        # Legacy audit snapshots may contain explicit URLs saved before this
        # field was sanitized. Preserve history while masking it at read time.
        for field in ('before', 'after'):
            snapshot = row[field]
            if isinstance(snapshot, dict) and 'git_remote' in snapshot:
                row[field] = {**snapshot, 'git_remote': safe_project_git_remote(snapshot['git_remote'])}
    return row


def require_project(db, project_id):
    obj = db.get(Project, project_id)
    if obj is None: raise HTTPException(404, 'Project not found')
    return obj


def scoped(db, cls, project_id, entity_id):
    require_project(db, project_id)
    obj = db.scalar(select(cls).where(cls.id == entity_id, cls.project_id == project_id))
    if obj is None: raise HTTPException(404, 'Record not found in project')
    return obj


def records(db, cls, project_id):
    ordering = (cls.pinned.desc(), cls.id) if cls is Task else (cls.id,)
    return list(db.scalars(select(cls).where(cls.project_id == project_id).order_by(*ordering)))


def question_dict(db, q):
    return {**serialize(q), 'answers': [serialize(a) for a in db.scalars(select(Answer).where(Answer.question_id == q.id, Answer.project_id == q.project_id).order_by(Answer.id))]}


def audit(db, project_id, obj, action, before=None):
    db.flush()
    db.add(AuditEvent(project_id=project_id, entity_type=obj.__tablename__, entity_id=obj.id, action=action, before=before, after=serialize(obj)))
    project = require_project(db, project_id)
    project.updated_at = utcnow()


def validate_repository(value, *, env=None):
    if value is None or value == '': return None
    try:
        path = Path(value).expanduser()
    except (RuntimeError, OSError) as exc:
        raise HTTPException(422, 'Repository user home could not be resolved') from exc
    if not path.is_absolute() or '..' in path.parts or any(ord(c) < 32 for c in str(path)):
        raise HTTPException(422, 'Repository must be an absolute path without traversal')
    # Reject symlinks in every component, so registration cannot resolve elsewhere.
    for component in [path, *path.parents]:
        if component.is_symlink(): raise HTTPException(422, 'Symlinked repository paths are not supported')
    if not path.is_dir() or str(path) == '/': raise HTTPException(422, 'Repository directory does not exist or is unsafe')
    try:
        result = subprocess.run(['git', '-C', str(path), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, timeout=5, env=env if env is not None else {**os.environ, 'GIT_OPTIONAL_LOCKS':'0'})
    except (OSError, subprocess.TimeoutExpired): raise HTTPException(422, 'Unable to inspect repository')
    if result.returncode or Path(result.stdout.strip()).resolve() != path.resolve():
        raise HTTPException(422, 'Path must be the root of an existing Git repository')
    return str(path)


def _repository_env():
    env = {key: val for key, val in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1', GIT_ALLOW_PROTOCOL='')
    return env


def repository_github_target(remote, path=None, *, validated=False):
    if remote.strip():
        return github_repository(safe_project_git_remote(remote))
    if not path:
        return None
    env = _repository_env()
    if not validated:
        path = validate_repository(path, env=env)
    from .project_evidence import _git, EvidenceReadError
    try:
        output, code = _git(path, 'config', '--local', '--no-includes', '--get', 'remote.origin.url',
                            allowed=(0, 1), limit=8192)
    except EvidenceReadError:
        return None
    return github_repository(output.decode('utf-8', errors='replace')) if code == 0 else None


_REMOTE_HOST = r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?'


def _safe_remote_address(name, raw):
    value = raw.strip()
    if not value or '\\' in value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    is_url = '://' in value
    if is_url:
        try:
            remote = urlsplit(value)
            host, port = remote.hostname, remote.port
        except ValueError:
            return None
        if (remote.scheme not in ('https', 'http', 'ssh', 'git') or not host
                or remote.netloc.count('@') > 1 or port == 0):
            return None
        # urlsplit validates bracketed IPv6. Keep only clear host and path
        # characters so the reconstructed URL cannot reinterpret credentials.
        host_pattern = r'[a-f0-9:.]+' if ':' in host else _REMOTE_HOST
        if not re.fullmatch(host_pattern, host) or '..' in host:
            return None
        path = remote.path
        host_display = f'[{host}]' if ':' in host else host
        port_suffix = f':{port}' if port is not None else ''
        address = f'{remote.scheme}://{host_display}{port_suffix}{path}'
        web_url = address if remote.scheme == 'https' else None
    else:
        # SCP-style SSH paths are relative to the remote account by default.
        # Preserve that syntax while removing the login name and URL suffixes.
        match = re.fullmatch(r'(?:[a-zA-Z0-9._-]+@)?([a-zA-Z0-9.-]+):([^?#]+)(?:[?#].*)?', value)
        if match is None:
            return None
        host, path = match.groups()
        host = host.lower()
        if (not re.fullmatch(_REMOTE_HOST, host) or '..' in host
                or host in ('https', 'http', 'ssh', 'git', 'file')):
            return None
        address, port, web_url = f'{host}:{path}', None, None
    path_pattern = r'(?:[a-zA-Z0-9._~/-]|%[a-fA-F0-9]{2})+' if is_url else r'[a-zA-Z0-9._~/-]+'
    if not re.fullmatch(path_pattern, path) or '//' in path or not path.strip('/'):
        return None
    # Keep URL escapes in the display value, but validate the decoded path
    # segments to rule out encoded separators, traversal and control characters.
    for segment in path.split('/'):
        try:
            decoded = unquote(segment, errors='strict')
        except UnicodeDecodeError:
            return None
        if decoded in ('.', '..') or any(
            not (char.isalnum() or char in ' ._~-') for char in decoded
        ):
            return None
    if host == 'github.com' and port is None:
        github = github_repository(f'https://github.com/{path.lstrip("/")}')
        if github:
            web_url = f'https://github.com/{github}'
    return {'name': name, 'address': address, **({'web_url': web_url} if web_url else {})}


def safe_project_git_remote(raw):
    """ProjectHub stores a display address, never Git authentication details."""
    remote = _safe_remote_address('project', raw) if isinstance(raw, str) else None
    if remote is None:
        return ''
    address = remote['address']
    # GitHub's credential-free web URL also keeps the Issues view available
    # when the original form used SSH, including an SCP-style URL.
    return address if address.startswith('https://') else remote.get('web_url', address)


def validate_project_git_remote(raw):
    sanitized = safe_project_git_remote(raw)
    if isinstance(raw, str) and raw.strip() and not sanitized:
        raise HTTPException(422, 'Use an HTTP(S), SSH, git://, or SCP-style Git remote address.')
    return sanitized


def _repository_remote_address(git, upstream_remote, registered_remote):
    preferred = dict.fromkeys((upstream_remote, 'origin'))

    def configured_address(name):
        if not name or not re.fullmatch(r'[a-zA-Z0-9._/-]+', name):
            return None
        configured = git('config', '--local', '--no-includes', '--null', '--get-all',
                         f'remote.{name}.url', optional=True, limit=8192)
        if configured is not None:
            # Git fetch uses the first configured URL for a remote.
            return _safe_remote_address(name, configured.stdout.partition('\0')[0])
        return None

    for name in preferred:
        address = configured_address(name)
        if address is not None:
            return address
    registered = _safe_remote_address('project', registered_remote)
    if registered is not None:
        return registered
    configured = git('config', '--local', '--no-includes', '--null', '--name-only', '--get-regexp',
                     r'^remote\..*\.url$', optional=True, limit=8192)
    if configured is None:
        return None
    names = {key.removeprefix('remote.').removesuffix('.url')
             for key in configured.stdout.split('\0') if key}
    if len(names) != 1:
        return None
    name = names.pop()
    return configured_address(name) if name not in preferred else None


def _repository_upstream(git, current_ref, head, overrides):
    if not current_ref:
        return None, False, None
    if head:
        tracked = git('for-each-ref', '--count=1', '--format=%(refname)%09%(upstream)%09%(upstream:remotename)',
                      os.fsdecode(current_ref), optional=True, raw=True)
        if tracked is None:
            return None, True, None
        current, _, data = tracked.stdout.rstrip(b'\n').partition(b'\t')
        upstream_ref, _, upstream_remote = data.partition(b'\t')
        if current != current_ref or not upstream_ref:
            return None, False, None
        name = upstream_ref.removeprefix(b'refs/remotes/').removeprefix(b'refs/heads/')
        target = os.fsdecode(upstream_ref)
        remote = upstream_remote.decode('utf-8', errors='replace') or None
    else:
        # A branch without a commit has no ref for for-each-ref. Git's status
        # header still reports the upstream configured for that branch.
        branch_name = current_ref.removeprefix(b'refs/heads/')
        status = git('status', '--porcelain=v2', '--branch', '-z', '--untracked-files=no',
                     '--ignore-submodules=all', '--no-renames', overrides=overrides, optional=True, raw=True)
        if status is None:
            return None, True, None
        headers = dict(entry.split(b' ', 2)[1:] for entry in status.stdout.split(b'\0')
                       if entry.startswith(b'# ') and entry.count(b' ') >= 2)
        if headers.get(b'branch.head') != branch_name:
            return None, True, None
        name = headers.get(b'branch.upstream')
        if not name:
            return None, False, None
        branch = os.fsdecode(branch_name)
        configured = git('config', '--local', '--no-includes', '--null', '--get', f'branch.{branch}.remote',
                         optional=True, limit=8192)
        remote = configured.stdout.removesuffix('\0') if configured is not None else None
        if remote is None:
            return None, True, None
        target = f'{branch}@{{upstream}}'
    if remote == '.':
        remote = None
    upstream = {'branch': name.decode('utf-8', errors='replace'),
                'is_remote': remote is not None,
                'ahead': None, 'behind': None, 'commit': None}
    tip = git('rev-parse', '--quiet', '--verify', '--end-of-options',
              target + '^{commit}', optional=True)
    if tip is None:
        return upstream, False, remote
    upstream_hash = tip.stdout.strip()
    commit = git('log', '-1', '--no-show-signature', '--format=%h%x09%cI', upstream_hash, '--', optional=True)
    if commit is not None:
        commit_hash, _, committed_at = commit.stdout.rstrip('\n').partition('\t')
        if commit_hash and committed_at:
            upstream['commit'] = {'hash': commit_hash, 'committed_at': committed_at}
    shallow = git('rev-parse', '--is-shallow-repository', optional=True)
    if head and shallow is not None and shallow.stdout.strip() == 'false':
        relation = git('rev-list', '--left-right', '--count', f'{head}...{upstream_hash}', '--', optional=True)
        if relation is not None:
            counts = relation.stdout.split()
            if len(counts) == 2 and all(count.isascii() and count.isdecimal() for count in counts):
                upstream['ahead'], upstream['behind'] = map(int, counts)
    return upstream, False, remote


def repository_snapshot(value, remote=''):
    if not value:
        raise HTTPException(404, 'No repository is linked to this project')
    env = _repository_env()
    path = validate_repository(value, env=env)
    def git(*args, allowed=(0,), overrides=(), optional=False, raw=False, limit=None):
        # Reuse the evidence inspector's byte/deadline bounds and safe process
        # cleanup instead of buffering arbitrary Git output in subprocess.run.
        from .project_evidence import _git, EvidenceReadError
        try:
            bounds = {} if limit is None else {'limit': limit}
            output, code = _git(path, *args, allowed=allowed, overrides=overrides, **bounds)
        except EvidenceReadError as exc:
            if optional:
                return None
            raise HTTPException(503, 'Repository inspection failed or exceeded its byte/time limit.') from exc
        return SimpleNamespace(stdout=output if raw else output.decode('utf-8', errors='replace'), returncode=code)

    branch = git('symbolic-ref', '--quiet', 'HEAD', allowed=(0, 1), raw=True)
    current_ref = branch.stdout.rstrip(b'\n') if branch.returncode == 0 else None
    current_branch = current_ref.removeprefix(b'refs/heads/').decode('utf-8', errors='replace') if current_ref else None
    branch_names = [name.removeprefix('refs/heads/') for name in git(
        'for-each-ref', '--format=%(refname)', '--sort=refname', '--count=301', 'refs/heads').stdout.split('\n') if name]
    branches_limited = len(branch_names) > 300
    branches = branch_names[:300]
    # An unborn current branch has no refs/heads entry yet. Also keep the current
    # branch visible when it falls beyond the bounded alphabetical listing.
    if current_branch and current_branch not in branches:
        if len(branches) == 300:
            branches_limited = True
            branches.pop()
        branches.append(current_branch)
        branches.sort()
    # Remote-tracking refs are already stored locally. Allow room for symbolic
    # aliases (such as origin/HEAD) within a bounded scan, but do not list aliases.
    remote_ref_limit = 600
    remote_refs = git('for-each-ref', '--format=%(refname)%09%(symref)', '--sort=refname',
                      f'--count={remote_ref_limit}', 'refs/remotes').stdout.rstrip('\n').split('\n')
    remote_branches = []
    for line in remote_refs:
        refname, _, target = line.partition('\t')
        if refname.startswith('refs/remotes/') and not target:
            remote_branches.append(refname.removeprefix('refs/remotes/'))
    remote_branches_limited = len(remote_branches) > 300 or len(remote_refs) == remote_ref_limit
    head_result = git('rev-parse', '--quiet', '--verify', '--end-of-options',
                      os.fsdecode(current_ref) if current_ref else 'HEAD', allowed=(0, 1))
    head = head_result.stdout.strip() if head_result.returncode == 0 else None
    commits = []
    if head:
        for line in git('log', '-3', '--no-show-signature', '--format=%h%x09%cI%x09%s', head, '--').stdout.split('\n'):
            fields = line.split('\t', 2)
            if len(fields) == 3:
                commit, committed_at, subject = fields
                commits.append({'hash': commit, 'subject': subject[:240], 'committed_at': committed_at})
    # Status may invoke configured clean/process filters when checking file contents.
    # Disable each driver rather than executing repository-defined commands.
    keys = git('config', '--null', '--name-only', '--get-regexp',
               r'^filter\..*\.(clean|process|required)$', allowed=(0, 1)).stdout.split('\0')
    drivers = sorted({key.rsplit('.', 1)[0] for key in keys if key})
    if any('=' in driver for driver in drivers):
        raise HTTPException(503, 'Unable to safely inspect configured Git filters')
    overrides = [item for driver in drivers for option in ('clean=', 'process=', 'required=false')
                 for item in ('-c', f'{driver}.{option}')]
    status = git('status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignore-submodules=dirty',
                 overrides=overrides)
    tracked_changes, untracked_files = 0, 0
    entries = iter(status.stdout.split('\0'))
    for entry in entries:
        if not entry:
            continue
        if len(entry) < 4 or entry[2] != ' ':
            raise HTTPException(503, 'Unable to parse repository status')
        code = entry[:2]
        if code == '??':
            untracked_files += 1
        else:
            tracked_changes += 1
        # In NUL-separated porcelain output renames/copies include a second
        # pathname. It belongs to the same changed file, not another record.
        if 'R' in code or 'C' in code:
            if not next(entries, None):
                raise HTTPException(503, 'Unable to parse repository status')
    target = repository_github_target(remote, path, validated=True)
    index_paths = git('ls-files', '--cached', '-z', optional=True, raw=True)
    tracked_files = None if index_paths is None else len(set(filter(None, index_paths.stdout.split(b'\0'))))
    upstream, upstream_unavailable, upstream_remote = _repository_upstream(git, current_ref, head, overrides)
    return {'branch': current_branch, 'branches': branches, 'branches_limited': branches_limited,
            'remote_branches': remote_branches[:300], 'remote_branches_limited': remote_branches_limited,
            'dirty': bool(status.stdout), 'commits': commits,
            'tracked_files': tracked_files, 'tracked_changes': tracked_changes, 'untracked_files': untracked_files,
            'upstream': upstream, **({'upstream_unavailable': True} if upstream_unavailable else {}),
            'remote_repository': _repository_remote_address(git, upstream_remote, remote),
            'github_issues_url': issues_url(target) if target else None}


def begin_session_write(db):
    """Keep session ownership checks and subsequent writes atomic with moves."""
    connection = db.connection().connection.driver_connection
    if not connection.in_transaction:
        db.execute(text('BEGIN IMMEDIATE'))


def validate_links(db, project_id, values):
    if values.get('session_id') is not None:
        begin_session_write(db)
    for key, cls in [('question_id', Question), ('decision_id', Decision), ('session_id', CodexSession), ('answer_id', Answer)]:
        if values.get(key) is not None: scoped(db, cls, project_id, values[key])
    if values.get('answer_id') is not None and values.get('question_id') is not None:
        if scoped(db, Answer, project_id, values['answer_id']).question_id != values['question_id']:
            raise HTTPException(422, 'Answer does not belong to the linked question')


def overview(db, project_id, *, compact=False, note_content=True):
    data = {'project': serialize(require_project(db, project_id))}
    if not compact:
        data['questions'] = [question_dict(db, q) for q in records(db, Question, project_id)]
        data['decisions'] = [serialize(d) for d in records(db, Decision, project_id)]
    data['tasks'] = [serialize(t) for t in records(db, Task, project_id)]
    if note_content:
        data['notes'] = [serialize(n) for n in records(db, Note, project_id)]
    else:
        # Select metadata directly: deferring ORM content but serializing the
        # entity would accidentally lazy-load every complete transcript again.
        columns = [column for column in Note.__table__.columns if column.name != 'content']
        data['notes'] = [{**dict(row), 'content_loaded': False} for row in db.execute(
            select(*columns).where(Note.project_id == project_id).order_by(Note.id)
        ).mappings()]
    if not compact:
        data['sessions'] = [serialize(s) for s in records(db, CodexSession, project_id)]
    if note_content:
        data['activity'] = [serialize(a) for a in db.scalars(
            select(AuditEvent).where(AuditEvent.project_id == project_id).order_by(AuditEvent.id.desc()).limit(50)
        )]
    else:
        # Audit snapshots can contain another full copy of a note/transcript.
        # Activity navigation needs only metadata; retain the stored audit intact.
        columns = [column for column in AuditEvent.__table__.columns if column.name not in ('before', 'after')]
        data['activity'] = [{**dict(row), 'details_loaded': False} for row in db.execute(
            select(*columns).where(AuditEvent.project_id == project_id).order_by(AuditEvent.id.desc()).limit(50)
        ).mappings()]
    return data


def summary(db, project_id):
    data = {'project': serialize(require_project(db, project_id)),
            'tasks': [dict(row) for row in db.execute(select(Task.id, Task.title, Task.status)
                .where(Task.project_id == project_id).order_by(Task.pinned.desc(), Task.id)).mappings()],
            'decisions': [dict(row) for row in db.execute(select(Decision.id, Decision.title, Decision.description, Decision.status, Decision.limitations)
                .where(Decision.project_id == project_id, Decision.status == 'accepted').order_by(Decision.id)).mappings()],
            'questions': [dict(row) for row in db.execute(select(Question.id, Question.title, Question.status)
                .where(Question.project_id == project_id, Question.status != 'resolved').order_by(Question.id)).mappings()],
            'notes': [{'kind': 'limitation', 'content': content} for content in db.scalars(
                select(Note.content).where(Note.project_id == project_id, Note.kind == 'limitation').order_by(Note.id))]}
    p = data['project']
    decisions = [d for d in data['decisions'] if d['status'] == 'accepted']
    questions = [q for q in data['questions'] if q['status'] != 'resolved']
    tasks = [t for t in data['tasks'] if t['status'] not in ('completed', 'not_planned')]
    limitations = [n['content'] for n in data['notes'] if n['kind'] == 'limitation'] + [d['limitations'] for d in decisions if d['limitations']]
    next_actions = [f"Resolve question #{q['id']}: {q['title']}" for q in questions] + [f"Task #{t['id']}: {t['title']} ({t['status']})" for t in tasks]
    sections = {
        'objective': p['goals'] or p['description'] or 'No objective recorded.',
        'architecture': p['architecture_mermaid'] or 'No architecture recorded.',
        'implemented_functionality': [t['title'] for t in data['tasks'] if t['status']=='completed'],
        'accepted_decisions': [f"#{d['id']} {d['title']}: {d['description']}" for d in decisions],
        'open_questions': [f"#{q['id']} {q['title']}" for q in questions],
        'current_tasks': [f"#{t['id']} {t['title']} ({t['status']})" for t in tasks],
        'known_limitations': limitations,
        'recommended_next_actions': next_actions or ['Record or validate the next project milestone.'],
    }
    markdown = f"# {p['name']} — project summary\n\nSource: deterministic database records; recommendations are rule based.\n"
    for key,value in sections.items():
        markdown += '\n## ' + key.replace('_',' ').title() + '\n\n'
        markdown += (value if isinstance(value,str) else '\n'.join('- '+x for x in value) or 'None recorded.') + '\n'
    return {'project_id':project_id, 'source':'deterministic', 'sections': sections, 'markdown':markdown}


def review(db, project_id):
    require_project(db, project_id)
    tasks = db.execute(select(Task.id, Task.title, Task.status).where(Task.project_id == project_id)
                       .order_by(Task.pinned.desc(), Task.id)).mappings()
    findings = []
    def finding(category, message, entity_id=None): findings.append({'category':category, 'message':message, 'entity_id':entity_id, 'status':'unverified'})
    for t in tasks:
        if t['status'] not in ('completed', 'not_planned'): finding('incomplete_task',f"Task #{t['id']} is {t['status']}: {t['title']}",t['id'])
    for n in db.execute(select(Note.id, Note.content).where(Note.project_id == project_id, Note.kind == 'limitation')
                        .order_by(Note.id)).mappings():
        finding('limitation',n['content'],n['id'])
    return {'project_id':project_id,'source':'deterministic','findings':findings,'limitations':['Rule-based checks flag gaps in recorded information; they do not verify claims or detect contradictions in meaning.']}
