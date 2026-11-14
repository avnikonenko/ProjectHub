import os
import subprocess
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import select
from .integrations.github_issues import github_repository, issues_url
from .models import Project, Question, Answer, Decision, Task, Note, AuditEvent, CodexSession, utcnow


def serialize(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


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
    path = Path(value).expanduser()
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
        return github_repository(remote)
    if not path:
        return None
    env = _repository_env()
    if not validated:
        path = validate_repository(path, env=env)
    try:
        result = subprocess.run(
            ['git', '-C', path, 'config', '--local', '--no-includes', '--get', 'remote.origin.url'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return github_repository(result.stdout) if result.returncode == 0 else None


def repository_snapshot(value, remote=''):
    if not value:
        raise HTTPException(404, 'No repository is linked to this project')
    env = _repository_env()
    path = validate_repository(value, env=env)
    command = ['git', '-C', path, '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null']

    def git(*args, allowed=(0,), overrides=()):
        try:
            result = subprocess.run([*command, *overrides, *args], capture_output=True,
                                    text=True, encoding='utf-8', errors='replace', timeout=5, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(503, 'Unable to inspect repository') from exc
        if result.returncode not in allowed:
            raise HTTPException(503, 'Unable to inspect repository')
        return result

    branch = git('symbolic-ref', '--quiet', '--short', 'HEAD', allowed=(0, 1))
    head = git('rev-parse', '--quiet', '--verify', 'HEAD', allowed=(0, 1))
    commits = []
    if head.returncode == 0:
        for line in git('log', '-3', '--no-show-signature', '--format=%h%x09%cI%x09%s', 'HEAD', '--').stdout.split('\n'):
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
    return {'branch': branch.stdout.strip() if branch.returncode == 0 else None,
            'dirty': bool(status.stdout), 'commits': commits,
            'tracked_changes': tracked_changes, 'untracked_files': untracked_files,
            'github_issues_url': issues_url(target) if target else None}


def validate_links(db, project_id, values):
    for key, cls in [('question_id', Question), ('decision_id', Decision), ('session_id', CodexSession), ('answer_id', Answer)]:
        if values.get(key) is not None: scoped(db, cls, project_id, values[key])
    if values.get('answer_id') is not None and values.get('question_id') is not None:
        if scoped(db, Answer, project_id, values['answer_id']).question_id != values['question_id']:
            raise HTTPException(422, 'Answer does not belong to the linked question')


def overview(db, project_id, *, compact=False):
    data = {'project': serialize(require_project(db, project_id))}
    if not compact:
        data['questions'] = [question_dict(db, q) for q in records(db, Question, project_id)]
        data['decisions'] = [serialize(d) for d in records(db, Decision, project_id)]
    data['tasks'] = [serialize(t) for t in records(db, Task, project_id)]
    data['notes'] = [serialize(n) for n in records(db, Note, project_id)]
    if not compact:
        data['sessions'] = [serialize(s) for s in records(db, CodexSession, project_id)]
    data['activity'] = [serialize(a) for a in db.scalars(
        select(AuditEvent).where(AuditEvent.project_id == project_id).order_by(AuditEvent.id.desc()).limit(50)
    )]
    return data


def summary(db, project_id):
    data = overview(db,project_id)
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
    data = overview(db,project_id)
    findings = []
    def finding(category, message, entity_id=None): findings.append({'category':category, 'message':message, 'entity_id':entity_id, 'status':'unverified'})
    for t in data['tasks']:
        if t['status'] not in ('completed', 'not_planned'): finding('incomplete_task',f"Task #{t['id']} is {t['status']}: {t['title']}",t['id'])
    for n in data['notes']:
        if n['kind']=='limitation': finding('limitation',n['content'],n['id'])
    return {'project_id':project_id,'source':'deterministic','findings':findings,'limitations':['Rule-based checks flag gaps in recorded information; they do not verify claims or detect contradictions in meaning.']}
