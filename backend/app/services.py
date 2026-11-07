import os
import subprocess
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import select
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
    return list(db.scalars(select(cls).where(cls.project_id == project_id).order_by(cls.id)))


def question_dict(db, q):
    return {**serialize(q), 'answers': [serialize(a) for a in db.scalars(select(Answer).where(Answer.question_id == q.id, Answer.project_id == q.project_id).order_by(Answer.id))]}


def audit(db, project_id, obj, action, before=None):
    db.flush()
    db.add(AuditEvent(project_id=project_id, entity_type=obj.__tablename__, entity_id=obj.id, action=action, before=before, after=serialize(obj)))
    project = require_project(db, project_id)
    project.updated_at = utcnow()


def validate_repository(value):
    if value is None or value == '': return None
    path = Path(value).expanduser()
    if not path.is_absolute() or '..' in path.parts or any(ord(c) < 32 for c in str(path)):
        raise HTTPException(422, 'Repository must be an absolute path without traversal')
    # Reject symlinks in every component, so registration cannot resolve elsewhere.
    for component in [path, *path.parents]:
        if component.is_symlink(): raise HTTPException(422, 'Symlinked repository paths are not supported')
    if not path.is_dir() or str(path) == '/': raise HTTPException(422, 'Repository directory does not exist or is unsafe')
    try:
        result = subprocess.run(['git', '-C', str(path), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, timeout=5, env={**os.environ, 'GIT_OPTIONAL_LOCKS':'0'})
    except (OSError, subprocess.TimeoutExpired): raise HTTPException(422, 'Unable to inspect repository')
    if result.returncode or Path(result.stdout.strip()).resolve() != path.resolve():
        raise HTTPException(422, 'Path must be the root of an existing Git repository')
    return str(path)


def validate_links(db, project_id, values):
    for key, cls in [('question_id', Question), ('decision_id', Decision), ('session_id', CodexSession), ('answer_id', Answer)]:
        if values.get(key) is not None: scoped(db, cls, project_id, values[key])
    if values.get('answer_id') is not None and values.get('question_id') is not None:
        if scoped(db, Answer, project_id, values['answer_id']).question_id != values['question_id']:
            raise HTTPException(422, 'Answer does not belong to the linked question')


def overview(db, project_id):
    p = require_project(db, project_id)
    return {'project': serialize(p), 'questions': [question_dict(db,q) for q in records(db,Question,project_id)], 'decisions':[serialize(d) for d in records(db,Decision,project_id)], 'tasks':[serialize(t) for t in records(db,Task,project_id)], 'notes':[serialize(n) for n in records(db,Note,project_id)], 'sessions':[serialize(s) for s in records(db,CodexSession,project_id)], 'activity':[serialize(a) for a in db.scalars(select(AuditEvent).where(AuditEvent.project_id == project_id).order_by(AuditEvent.id.desc()).limit(50))]}


def summary(db, project_id):
    data = overview(db,project_id)
    p = data['project']
    decisions = [d for d in data['decisions'] if d['status'] == 'accepted']
    questions = [q for q in data['questions'] if q['status'] != 'resolved']
    tasks = [t for t in data['tasks'] if t['status'] != 'completed']
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
        if t['status']!='completed': finding('incomplete_task',f"Task #{t['id']} is {t['status']}: {t['title']}",t['id'])
    for n in data['notes']:
        if n['kind']=='limitation': finding('limitation',n['content'],n['id'])
    return {'project_id':project_id,'source':'deterministic','findings':findings,'limitations':['Rule-based checks flag gaps in recorded information; they do not verify claims or detect contradictions in meaning.']}
