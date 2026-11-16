"""Local ProjectHub HTTP API and static web application."""
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path
from urllib.parse import urlparse
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .db import initialize, default_data_dir, get_db
from .integrations import chatgpt_share, github_issues
from .integrations.codex.history_search import search_history
from .project_search import project_search
from .models import *
from .schemas import *
from .services import serialize, require_project, scoped, records, question_dict, audit, validate_repository, validate_links, overview, summary, review
from .services import repository_snapshot, repository_github_target
from .version import VERSION


def db_dependency(request: Request):
    yield from get_db(request)


def create_app(data_dir=None):
    app = FastAPI(title='ProjectHub', version=VERSION)
    engine, directory = initialize(data_dir or default_data_dir())
    app.state.engine, app.state.data_dir = engine, directory
    db_dep = Depends(db_dependency)

    @app.middleware('http')
    async def local_only(request, call_next):
        host = request.url.hostname
        if host not in ('127.0.0.1','localhost','::1','testserver'):
            return JSONResponse({'detail':'Unrecognized local host'},status_code=403)
        origin = request.headers.get('origin')
        if origin:
            try:
                parsed = urlparse(origin)
                same_origin = parsed.scheme == request.url.scheme and parsed.netloc == request.url.netloc
                dev_origin = origin in ('http://localhost:5173','http://127.0.0.1:5173','http://[::1]:5173')
                allowed = (same_origin or dev_origin) and not parsed.username and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment
                # Force validation of malformed numeric ports as well.
                parsed.port
            except ValueError:
                allowed = False
            if not allowed: return JSONResponse({'detail':'Cross-origin access denied'},status_code=403)
        if request.method not in ('GET','HEAD','OPTIONS'):
            if request.headers.get('sec-fetch-site') == 'cross-site': return JSONResponse({'detail':'Cross-site request denied'},status_code=403)
            # Bound request bytes before JSON or multipart parsing, including chunked input.
            limit = 3 * 1024 * 1024
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit: return JSONResponse({'detail':'Request exceeds 3 MiB'},status_code=413)
                chunks.append(chunk)
            request._body = b''.join(chunks)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    @app.get('/api/health')
    def health(): return {'status':'ok','version':VERSION}

    @app.get('/api/projects')
    def list_projects(compact: bool = False, db: Session = db_dep):
        if compact:
            return [{'id': row.id, 'name': row.name} for row in
                    db.execute(select(Project.id, Project.name).order_by(Project.id))]
        return [serialize(p) for p in db.scalars(select(Project).order_by(Project.id))]

    @app.post('/api/projects',status_code=201)
    def create_project(payload: ProjectCreate,db: Session=db_dep):
        values=payload.model_dump(); values['repository_path']=validate_repository(values['repository_path'])
        p=Project(**values);db.add(p);db.flush();audit(db,p.id,p,'created');db.commit();return serialize(p)

    @app.get('/api/projects/{project_id}')
    def get_project(project_id:int,db:Session=db_dep): return serialize(require_project(db,project_id))

    @app.patch('/api/projects/{project_id}')
    def update_project(project_id:int,payload:ProjectPatch,db:Session=db_dep):
        p=require_project(db,project_id);before=serialize(p);values=payload.model_dump(exclude_unset=True)
        if 'repository_path' in values: values['repository_path']=validate_repository(values['repository_path'])
        apply_values(p,values);audit(db,project_id,p,'updated',before);db.commit();return serialize(p)

    @app.get('/api/projects/{project_id}/overview')
    def get_overview(project_id: int, compact: bool = False, note_content: bool = True, db: Session = db_dep):
        return overview(db, project_id, compact=compact, note_content=note_content)

    @app.get('/api/projects/{project_id}/questions')
    def get_questions(project_id:int,db:Session=db_dep):require_project(db,project_id);return [question_dict(db,q) for q in records(db,Question,project_id)]

    @app.post('/api/projects/{project_id}/questions',status_code=201)
    def create_question(project_id:int,payload:QuestionCreate,db:Session=db_dep):
        require_project(db,project_id)
        if payload.status=='resolved':raise HTTPException(422,'Resolve questions by accepting an answer')
        q=Question(project_id=project_id,**payload.model_dump());db.add(q);audit(db,project_id,q,'created');db.commit();return question_dict(db,q)

    @app.patch('/api/projects/{project_id}/questions/{question_id}')
    def update_question(project_id:int,question_id:int,payload:QuestionPatch,db:Session=db_dep):
        # Serialize the resolution-state check with acceptance and reopening.
        db.execute(text('BEGIN IMMEDIATE'))
        q=scoped(db,Question,project_id,question_id);values=payload.model_dump(exclude_unset=True)
        if 'status' in values and values['status']!=q.status and (values['status']=='resolved' or q.accepted_answer_id is not None):raise HTTPException(422,'Use accept/reopen to change resolution state')
        before=serialize(q);apply_values(q,values);audit(db,project_id,q,'updated',before);db.commit();return question_dict(db,q)

    @app.post('/api/projects/{project_id}/questions/{question_id}/answers',status_code=201)
    def create_answer(project_id:int,question_id:int,payload:AnswerCreate,db:Session=db_dep):
        scoped(db,Question,project_id,question_id);a=Answer(project_id=project_id,question_id=question_id,**payload.model_dump());db.add(a);audit(db,project_id,a,'proposed');db.commit();return serialize(a)

    @app.post('/api/projects/{project_id}/questions/{question_id}/accept')
    def accept_answer(project_id:int,question_id:int,payload:AcceptAnswer,db:Session=db_dep):
        # Acquire SQLite's write lock before checking state, preventing concurrent double acceptance.
        db.execute(text('BEGIN IMMEDIATE'))
        q=scoped(db,Question,project_id,question_id);a=scoped(db,Answer,project_id,payload.answer_id)
        if a.question_id!=q.id:raise HTTPException(422,'Answer does not belong to this question')
        current=db.scalar(select(Decision).where(Decision.project_id==project_id,Decision.question_id==q.id,Decision.status=='accepted').order_by(Decision.id.desc()))
        if q.accepted_answer_id==a.id and current and current.answer_id==a.id:
            task=db.scalar(select(Task).where(Task.project_id==project_id,Task.decision_id==current.id).order_by(Task.id))
            if payload.create_task and task is None: task=make_task(db,project_id,current,{})
            db.commit();return {'question':question_dict(db,q),'decision':serialize(current),'task':serialize(task) if task else None}
        for d in db.scalars(select(Decision).where(Decision.project_id==project_id,Decision.question_id==q.id,Decision.status=='accepted')):
            before=serialize(d);d.status='superseded';audit(db,project_id,d,'superseded',before)
        for previous in db.scalars(select(Answer).where(Answer.project_id==project_id,Answer.question_id==q.id,Answer.status=='accepted')):
            before=serialize(previous);previous.status='proposed';audit(db,project_id,previous,'acceptance_removed',before)
        before=serialize(a);a.status='accepted';audit(db,project_id,a,'accepted',before)
        before=serialize(q);q.accepted_answer_id=a.id;q.status='resolved';audit(db,project_id,q,'resolved',before)
        alternatives='\n'.join(x.solution for x in db.scalars(select(Answer).where(Answer.project_id==project_id,Answer.question_id==q.id,Answer.id!=a.id).order_by(Answer.id)))
        d=Decision(project_id=project_id,title=q.title,description=a.solution,next_step=payload.next_step,rationale=a.explanation,alternatives=alternatives,evidence=a.evidence,limitations=a.limitations,status='accepted',question_id=q.id,answer_id=a.id)
        db.add(d);audit(db,project_id,d,'accepted_from_answer');task=make_task(db,project_id,d,{}) if payload.create_task else None
        db.commit();return {'question':question_dict(db,q),'decision':serialize(d),'task':serialize(task) if task else None}

    @app.post('/api/projects/{project_id}/questions/{question_id}/reopen')
    def reopen(project_id:int,question_id:int,db:Session=db_dep):
        db.execute(text('BEGIN IMMEDIATE'));q=scoped(db,Question,project_id,question_id);before=serialize(q)
        if q.accepted_answer_id:
            a=scoped(db,Answer,project_id,q.accepted_answer_id);old=serialize(a);a.status='proposed';audit(db,project_id,a,'acceptance_removed',old)
        for d in db.scalars(select(Decision).where(Decision.project_id==project_id,Decision.question_id==q.id,Decision.status=='accepted')):
            old=serialize(d);d.status='superseded';audit(db,project_id,d,'superseded_on_reopen',old)
        q.accepted_answer_id=None;q.status='reopened';audit(db,project_id,q,'reopened',before);db.commit();return question_dict(db,q)

    @app.get('/api/projects/{project_id}/decisions')
    def get_decisions(project_id:int,db:Session=db_dep):require_project(db,project_id);return [serialize(d) for d in records(db,Decision,project_id)]

    @app.post('/api/projects/{project_id}/decisions',status_code=201)
    def create_decision(project_id:int,payload:DecisionCreate,db:Session=db_dep):
        require_project(db,project_id);values=payload.model_dump();validate_links(db,project_id,values)
        if values.get('answer_id') is not None:raise HTTPException(422,'Create answer-linked decisions through answer acceptance')
        d=Decision(project_id=project_id,**values);db.add(d);audit(db,project_id,d,'created');db.commit();return serialize(d)

    @app.patch('/api/projects/{project_id}/decisions/{decision_id}')
    def update_decision(project_id:int,decision_id:int,payload:DecisionPatch,db:Session=db_dep):
        d=scoped(db,Decision,project_id,decision_id);values=payload.model_dump(exclude_unset=True);validate_links(db,project_id,values)
        if 'question_id' in values or 'answer_id' in values:raise HTTPException(422,'Decision provenance cannot be reassigned')
        if d.status=='superseded' and values.get('status')=='accepted':raise HTTPException(422,'Create a new decision revision instead of restoring superseded decisions')
        if d.answer_id and 'status' in values and values['status']!=d.status:raise HTTPException(422,'Use question reopen/accept for linked decision status changes')
        before=serialize(d);apply_values(d,values);audit(db,project_id,d,'revised',before);db.commit();return serialize(d)

    @app.post('/api/projects/{project_id}/decisions/{decision_id}/tasks',status_code=201)
    def decision_task(project_id:int,decision_id:int,payload:DecisionTask,db:Session=db_dep):
        d=scoped(db,Decision,project_id,decision_id)
        if d.status!='accepted':raise HTTPException(409,'Only accepted decisions can create implementation tasks')
        t=make_task(db,project_id,d,payload.model_dump(exclude_none=True));db.commit();return serialize(t)

    @app.get('/api/projects/{project_id}/tasks')
    def get_tasks(project_id:int,db:Session=db_dep):require_project(db,project_id);return [serialize(t) for t in records(db,Task,project_id)]

    @app.post('/api/projects/{project_id}/tasks',status_code=201)
    def create_task(project_id:int,payload:TaskCreate,db:Session=db_dep):
        require_project(db,project_id);values=payload.model_dump();validate_links(db,project_id,values);t=Task(project_id=project_id,**values);db.add(t);audit(db,project_id,t,'created');db.commit();return serialize(t)

    @app.post('/api/projects/{project_id}/tasks/bulk', status_code=201)
    def create_tasks(project_id: int, payload: TaskBulkCreate, db: Session = db_dep):
        require_project(db, project_id)
        values = [item.model_dump() for item in payload.items]
        for item in values:
            validate_links(db, project_id, item)
        tasks = []
        for item in values:
            task = Task(project_id=project_id, **item)
            db.add(task)
            audit(db, project_id, task, 'created')
            tasks.append(task)
        db.commit()
        return [serialize(task) for task in tasks]

    @app.patch('/api/projects/{project_id}/tasks/{task_id}')
    def update_task(project_id:int,task_id:int,payload:TaskPatch,db:Session=db_dep):
        t=scoped(db,Task,project_id,task_id);values=payload.model_dump(exclude_unset=True);validate_links(db,project_id,{**serialize(t),**values});before=serialize(t);apply_values(t,values);audit(db,project_id,t,'updated',before);db.commit();return serialize(t)

    @app.delete('/api/projects/{project_id}/tasks/{task_id}', status_code=204)
    def delete_task(project_id: int, task_id: int, db: Session = db_dep):
        task = scoped(db, Task, project_id, task_id)
        audit(db, project_id, task, 'deleted', serialize(task))
        db.delete(task)
        db.commit()
        return Response(status_code=204)

    @app.get('/api/projects/{project_id}/notes')
    def get_notes(project_id:int,db:Session=db_dep):require_project(db,project_id);return [serialize(n) for n in records(db,Note,project_id)]

    @app.get('/api/projects/{project_id}/notes/{note_id}')
    def get_note(project_id: int, note_id: int, db: Session = db_dep):
        return serialize(scoped(db, Note, project_id, note_id))

    @app.post('/api/projects/{project_id}/notes',status_code=201)
    def create_note(project_id:int,payload:NoteCreate,db:Session=db_dep):
        require_project(db,project_id);n=Note(project_id=project_id,**payload.model_dump());db.add(n);audit(db,project_id,n,'created');db.commit();return serialize(n)

    @app.post('/api/projects/{project_id}/notes/chatgpt-share', status_code=201)
    def import_chatgpt_share(project_id: int, payload: ChatGPTShareImport, db: Session = db_dep):
        require_project(db, project_id)
        try:
            url = chatgpt_share.canonical_url(payload.url)
        except ValueError as exc:
            raise HTTPException(422, 'Enter a public https://chatgpt.com/share/ conversation link.') from exc
        existing = select(Note.id).where(
            Note.project_id == project_id, Note.kind == 'document', Note.reference == url,
        ).limit(1)
        if db.scalar(existing) is not None:
            raise HTTPException(409, 'This shared conversation is already saved in this project.')
        try:
            title, content = chatgpt_share.read_transcript(url)
        except chatgpt_share.ShareReadError as exc:
            raise HTTPException(502, str(exc)) from exc
        note = Note(project_id=project_id, title=title, content=content, kind='document', reference=url)
        db.add(note)
        audit(db, project_id, note, 'created')
        db.commit()
        return serialize(note)

    @app.patch('/api/projects/{project_id}/notes/{note_id}')
    def update_note(project_id:int,note_id:int,payload:NotePatch,db:Session=db_dep):
        n=scoped(db,Note,project_id,note_id);values=payload.model_dump(exclude_unset=True);before=serialize(n);apply_values(n,values);audit(db,project_id,n,'updated',before);db.commit();return serialize(n)

    @app.delete('/api/projects/{project_id}/notes/{note_id}', status_code=204)
    def delete_note(project_id: int, note_id: int, db: Session = db_dep):
        note = scoped(db, Note, project_id, note_id)
        audit(db, project_id, note, 'deleted', serialize(note))
        db.delete(note)
        db.commit()
        return Response(status_code=204)

    @app.get('/api/projects/{project_id}/summary')
    def get_summary(project_id:int,db:Session=db_dep): return {**summary(db,project_id),'generated_at':utcnow()}

    @app.get('/api/projects/{project_id}/review')
    def get_review(project_id:int,db:Session=db_dep):
        result=review(db,project_id)
        result['findings']=[{**f,'severity':'warning' if f['category'] in ('missing_validation','possible_contradiction') else 'info','title':f['category'].replace('_',' ').title(),'detail':f['message']} for f in result['findings']]
        result['generated_at']=utcnow();result['markdown']='# Project review\n\nRule-based suggestions requiring verification.\n\n'+'\n'.join('- '+f['detail'] for f in result['findings'])
        return result

    @app.get('/api/projects/{project_id}/audit')
    def get_audit(project_id:int,db:Session=db_dep):
        require_project(db,project_id);return [serialize(a) for a in db.scalars(select(AuditEvent).where(AuditEvent.project_id==project_id).order_by(AuditEvent.id))]

    @app.get('/api/projects/{project_id}/repository')
    def get_repository_snapshot(project_id: int, db: Session = db_dep):
        project = require_project(db, project_id)
        path, remote = project.repository_path, project.git_remote
        db.rollback()
        return repository_snapshot(path, remote)

    @app.get('/api/projects/{project_id}/repository/issues')
    def get_repository_issues(project_id: int, db: Session = db_dep):
        project = require_project(db, project_id)
        path, remote = project.repository_path, project.git_remote
        db.rollback()
        repository = repository_github_target(remote, path)
        if not repository:
            raise HTTPException(404, 'No GitHub repository is linked to this project.')
        try:
            return github_issues.open_issues(repository)
        except github_issues.IssuesReadError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get('/api/projects/{project_id}/history/search')
    def search_conversations(project_id: int, q: str = Query('', max_length=2000),
                             offset: int = Query(0, ge=0, le=2_147_483_647), db: Session = db_dep):
        require_project(db, project_id)
        return search_history(db, project_id, q, offset, include_shared=True)

    @app.get('/api/projects/{project_id}/search/all')
    def search_all(project_id: int, q: str = Query('', max_length=2000),
                   kind: Literal['all', 'tasks', 'notes', 'conversations', 'qa', 'knowledge', 'sessions', 'other'] = 'all',
                   offset: int = Query(0, ge=0, le=2_147_483_647), db: Session = db_dep):
        return project_search(db, project_id, q, kind, offset)

    @app.get('/api/projects/{project_id}/search')
    def search(project_id:int,q:str='',db:Session=db_dep):
        require_project(db,project_id)
        terms=re.findall(r'\w+',q,flags=re.UNICODE)[:30]
        if not terms:return []
        query=' AND '.join('"'+term+'"' for term in terms)
        rows=db.execute(text("SELECT entity_type,entity_id,title,snippet(search_index,4,'[',']','…',30) AS snippet FROM search_index WHERE search_index MATCH :q AND project_id=:pid ORDER BY rank,entity_type,entity_id LIMIT 100"),{'q':query,'pid':project_id}).mappings()
        return [dict(row) for row in rows]

    @app.get('/api/projects/{project_id}/export')
    def export(project_id:int,db:Session=db_dep):
        data=overview(db,project_id);data.pop('activity');data['schema_version']=1
        for key,cls in [('answers',Answer),('audit',AuditEvent),('sessions',CodexSession),('contexts',ContextPackage),('deliveries',Delivery),('events',CodexEvent)]:data[key]=[serialize(x) for x in db.scalars(select(cls).where(cls.project_id==project_id).order_by(cls.id))]
        return data

    @app.post('/api/projects/{project_id}/documents',status_code=201)
    async def import_document(project_id:int,file:UploadFile=File(...),db:Session=db_dep):
        require_project(db,project_id);name=file.filename or ''
        if not re.fullmatch(r'[\w .()-]{1,180}',name) or name in ('.','..') or Path(name).suffix.lower() not in ('.txt','.md'):
            raise HTTPException(422,'Use a safe .txt or .md filename without paths')
        content=await file.read(2*1024*1024+1)
        if len(content)>2*1024*1024:raise HTTPException(413,'Documents are limited to 2 MiB')
        try: decoded=content.decode('utf-8')
        except UnicodeDecodeError:raise HTTPException(422,'Only UTF-8 text documents are supported')
        if '\x00' in decoded:raise HTTPException(422,'Binary content is not supported')
        uploads=directory/'uploads'
        if uploads.is_symlink():raise HTTPException(422,'Unsafe upload directory')
        uploads.mkdir(mode=0o700,exist_ok=True)
        stored=uuid.uuid4().hex+Path(name).suffix.lower();path=uploads/stored
        upload_fd=os.open(uploads,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            fd=os.open(stored,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=upload_fd)
        except Exception:
            os.close(upload_fd)
            raise
        try:
            with os.fdopen(fd,'wb') as handle:handle.write(content)
            n=Note(project_id=project_id,title=name,content=decoded,kind='document',reference=stored);db.add(n);audit(db,project_id,n,'imported');db.commit();return serialize(n)
        except Exception:
            os.unlink(stored,dir_fd=upload_fd);raise
        finally:
            os.close(upload_fd)

    @app.post('/api/backup')
    def backup():
        backups=directory/'backups'
        if backups.is_symlink():raise HTTPException(422,'Unsafe backup directory')
        backups.mkdir(mode=0o700,exist_ok=True)
        name='projecthub-'+utcnow().replace(':','').replace('+','_')+'-'+uuid.uuid4().hex[:8]+'.sqlite3';target=backups/name
        fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600);os.close(fd)
        with sqlite3.connect(directory/'projecthub.sqlite3') as src, sqlite3.connect(target) as dest:src.backup(dest)
        return {'filename':name,'path':str(target)}

    # Integration owns this optional router and its lifecycle; unavailable Codex never disables core.
    try:
        from .integrations.codex.router import router as codex_router, settings_router
        from .integrations.codex.manager import CodexManager
        from .integrations.codex.history_sync import HistorySync
        from .config import _boolean, _integer
        app.state.codex_manager = CodexManager(engine, directory)
        sync_enabled = _boolean(os.environ.get('PROJECTHUB_HISTORY_AUTO_SYNC', True), 'history.auto_sync')
        sync_interval = _integer(os.environ.get('PROJECTHUB_HISTORY_SYNC_INTERVAL', 60),
                                 'history.sync_interval', 30, 3600)
        app.state.history_sync = HistorySync(engine, lambda: app.state.codex_manager,
                                            interval=sync_interval) if sync_enabled else None
        app.include_router(codex_router)
        app.include_router(settings_router)
        @app.on_event('startup')
        def start_history_sync():
            if app.state.history_sync is not None:
                app.state.history_sync.start()

        @app.on_event('shutdown')
        def shutdown_codex():
            if app.state.history_sync is not None:
                app.state.history_sync.close()
            manager = getattr(app.state, 'codex_manager', None)
            if manager is not None: manager.close()
    except ModuleNotFoundError as exc:
        if exc.name not in ('backend.app.integrations','backend.app.integrations.codex','backend.app.integrations.codex.router'):raise

    frontend=Path(__file__).resolve().parents[2]/'frontend'/'dist'
    if frontend.is_dir():
        if (frontend/'assets').is_dir():app.mount('/assets',StaticFiles(directory=frontend/'assets'),name='assets')
        @app.get('/{path:path}')
        def web(path:str):
            if path.startswith('api/'):raise HTTPException(404,'API endpoint not found')
            return FileResponse(frontend/'index.html')
    @app.on_event('shutdown')
    def dispose_engine():
        engine.dispose()
    return app


def apply_values(obj,values):
    nullable={c.name for c in obj.__table__.columns if c.nullable}
    for key,value in values.items():
        if value is None and key not in nullable:raise HTTPException(422,f'{key} cannot be null')
        setattr(obj,key,value)


def make_task(db,project_id,decision,values):
    t=Task(project_id=project_id,title=values.get('title') or 'Implement: '+decision.title,description=values.get('description') if values.get('description') is not None else decision.description,priority=values.get('priority','medium'),status='ready',question_id=decision.question_id,decision_id=decision.id)
    db.add(t);audit(db,project_id,t,'created_from_decision');return t
