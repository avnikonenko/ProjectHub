"""Local ProjectHub HTTP API and static web application."""
import json
import os
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from urllib.parse import quote, urlparse
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .db import initialize, default_data_dir, get_db
from .integrations import chatgpt_share, conversation_import, github_issues
from . import conversation_ingest
from .integrations.codex.history_search import search_history
from .project_search import project_search
from .project_export import audit_response, project_archive_response, project_export_response
from .models import *
from .project_removal import ProjectRemovalGate, next_project_id, remove_project, retry_removed_uploads
from .schemas import *
from .services import serialize, require_project, scoped, records, question_dict, audit, validate_repository, validate_links, overview, summary, review
from .services import repository_snapshot, repository_github_target, validate_project_git_remote
from .resource_paths import runtime_resource
from .restart import install_restart, own_data_directory
from .version import VERSION


def db_dependency(request: Request):
    yield from get_db(request)


def create_app(data_dir=None, restart_controller=None, *, data_directory_owned=False):
    directory = data_dir or default_data_dir()
    ownership = nullcontext if data_directory_owned else lambda: own_data_directory(directory)
    # Keep factory-time database recovery safe even when invoked outside the
    # managed CLI. The lifespan also guards requests and shutdown.
    with ownership():
        return _create_app(directory, restart_controller, ownership)


def _create_app(data_dir, restart_controller, ownership):
    @asynccontextmanager
    async def lifespan(app):
        with ownership():
            try:
                history_sync = getattr(app.state, 'history_sync', None)
                if history_sync is not None:
                    history_sync.start()
                yield
            finally:
                try:
                    if history_sync is not None:
                        history_sync.close()
                    manager = getattr(app.state, 'codex_manager', None)
                    if manager is not None:
                        manager.close()
                finally:
                    engine.dispose()
                    if restart_controller is not None:
                        restart_controller.close()

    app = FastAPI(title='ProjectHub', version=VERSION, lifespan=lifespan)
    engine, directory = initialize(data_dir)
    from .integrations.codex.generation_attempts import interrupt_running
    interrupt_running(engine)
    with Session(engine) as db:
        retry_removed_uploads(db, directory)
    app.state.engine, app.state.data_dir = engine, directory
    db_dep = Depends(db_dependency)
    removal_gate = ProjectRemovalGate()
    install_restart(app, restart_controller)

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
        fetch_site = request.headers.get('sec-fetch-site')
        if (fetch_site in ('cross-site', 'same-site')
                and re.fullmatch(r'/api/projects/[^/]+/export(?:\.zip)?', request.url.path)):
            return JSONResponse({'detail': 'Cross-origin request denied'}, status_code=403)
        mutating = request.method not in ('GET','HEAD','OPTIONS')
        if mutating:
            if fetch_site == 'cross-site': return JSONResponse({'detail':'Cross-site request denied'},status_code=403)
            # Bound request bytes before JSON or multipart parsing, including chunked input.
            # Notes and conversation imports support 16 MiB of UTF-8 content.
            # JSON escaping can expand one byte to six; other writes keep the small cap.
            large_text_write = ((request.method == 'POST' and re.fullmatch(
                r'/api/projects/[1-9]\d*/(?:notes|conversations/(?:preview|import))', request.url.path))
                or (request.method == 'PATCH' and re.fullmatch(
                    r'/api/projects/[1-9]\d*/notes/[1-9]\d*', request.url.path)))
            limit = 3 * 1024 * 1024 + (6 * NOTE_CONTENT_MAX_BYTES if large_text_write else 0)
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit: return JSONResponse({'detail':f'Request exceeds {limit // (1024 * 1024)} MiB'},status_code=413)
                chunks.append(chunk)
            request._body = b''.join(chunks)
        if mutating:
            removal = request.method == 'DELETE' and re.fullmatch(r'/api/projects/[^/]+', request.url.path) is not None
            restart_guard = (restart_controller.mutation if restart_controller is not None
                             and request.url.path != '/api/settings/restart' else nullcontext)
            try:
                with removal_gate.mutation(removal=removal), restart_guard():
                    response = await call_next(request)
            except HTTPException as exc:
                return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
        else:
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
            return [{'id': row.id, 'name': row.name, 'pinned': row.pinned} for row in
                    db.execute(select(Project.id, Project.name, Project.pinned)
                               .order_by(Project.pinned.desc(), Project.id))]
        return [serialize(p) for p in db.scalars(select(Project).order_by(Project.pinned.desc(), Project.id))]

    @app.post('/api/projects',status_code=201)
    def create_project(payload: ProjectCreate,db: Session=db_dep):
        values=payload.model_dump(); values['repository_path']=validate_repository(values['repository_path'])
        values['git_remote'] = validate_project_git_remote(values['git_remote'])
        p=Project(id=next_project_id(db), **values);db.add(p);db.flush();audit(db,p.id,p,'created');db.commit();return serialize(p)

    @app.get('/api/projects/{project_id}')
    def get_project(project_id:int,db:Session=db_dep): return serialize(require_project(db,project_id))

    @app.delete('/api/projects/{project_id}', status_code=204)
    def delete_project(project_id: int, db: Session = db_dep):
        remove_project(db, directory, project_id, getattr(app.state, 'codex_manager', None))
        return Response(status_code=204)

    @app.get('/api/projects/{project_id}/agent/context')
    def get_agent_context(project_id: int, db: Session = db_dep,
                          q: str = Query('', max_length=2000)):
        from .agent_context import agent_context
        return agent_context(db, project_id, q)

    @app.get('/api/projects/{project_id}/agent/read')
    def get_agent_records(project_id: int, db: Session = db_dep,
                          kind: Literal['overview', 'knowledge', 'qa', 'scheme', 'notes',
                                        'tasks', 'sessions', 'responses'] = Query(...),
                          offset: int = Query(0, ge=0, le=2_147_483_647),
                          limit: int = Query(5, ge=1, le=20),
                          record_id: str | None = Query(None, min_length=1, max_length=200),
                          text_offset: int = Query(0, ge=0, le=2_147_483_647)):
        from .agent_context import agent_read
        return agent_read(db, project_id, kind, offset, limit, record_id, text_offset)

    @app.patch('/api/projects/{project_id}')
    def update_project(project_id:int,payload:ProjectPatch,db:Session=db_dep):
        p=require_project(db,project_id);before=serialize(p);values=payload.model_dump(exclude_unset=True)
        if 'repository_path' in values: values['repository_path']=validate_repository(values['repository_path'])
        if 'git_remote' in values: values['git_remote'] = validate_project_git_remote(values['git_remote'])
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
        # Serialize status validation and insertion with accept/reopen/revision.
        db.execute(text('BEGIN IMMEDIATE'))
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
        # Release the read connection while fetching. No write lock is held
        # during network access or parsing, which may be slow or fail.
        db.rollback()
        try:
            title, content = chatgpt_share.read_transcript(url)
            # Validate the extracted text, not just the downloaded page: indexed
            # stream references can expand a small page into a large transcript.
            values = NoteCreate(title=title, content=content, kind='document', reference=url).model_dump()
        except chatgpt_share.ShareReadError as exc:
            raise HTTPException(502, str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(502, 'The extracted conversation exceeds supported note limits or contains invalid text.') from exc
        # Another import can finish while this request is fetching. Recheck
        # ownership and uniqueness in the same write transaction as insertion.
        db.execute(text('BEGIN IMMEDIATE'))
        require_project(db, project_id)
        if db.scalar(existing) is not None:
            raise HTTPException(409, 'This shared conversation is already saved in this project.')
        note = Note(project_id=project_id, **values)
        db.add(note)
        audit(db, project_id, note, 'created')
        db.commit()
        return serialize(note)

    @app.patch('/api/projects/{project_id}/notes/{note_id}')
    def update_note(project_id:int,note_id:int,payload:NotePatch,db:Session=db_dep):
        n=scoped(db,Note,project_id,note_id);values=payload.model_dump(exclude_unset=True)
        if n.conversation is not None and any(key in values and values[key] != getattr(n, key)
                                              for key in ('content', 'kind', 'reference')):
            raise HTTPException(409, 'Use Import conversation to update this transcript and preserve its original source.')
        before=serialize(n);apply_values(n,values);audit(db,project_id,n,'updated',before);db.commit();return serialize(n)

    def prepare_conversation(project_id, payload, db):
        require_project(db, project_id)
        db.rollback()
        try:
            return conversation_ingest.prepare(payload, db.get_bind())
        except conversation_import.ConversationImportError as exc:
            status = {'access_denied': 403, 'share_unavailable': 502, 'session_not_found': 404,
                      'ambiguous_session': 409}.get(exc.code, 422)
            raise HTTPException(status, str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(422, 'The transcript exceeds supported limits or contains invalid text.') from exc

    @app.post('/api/projects/{project_id}/conversations/preview')
    def preview_conversation(project_id: int, payload: ConversationPreviewInput, db: Session = db_dep):
        values, source = prepare_conversation(project_id, payload, db)
        require_project(db, project_id)
        return conversation_ingest.preview(db, project_id, values, source)

    @app.post('/api/projects/{project_id}/conversations/import', status_code=201)
    def save_conversation(project_id: int, payload: ConversationSaveInput, db: Session = db_dep):
        values, source = prepare_conversation(project_id, payload, db)
        db.execute(text('BEGIN IMMEDIATE'))
        require_project(db, project_id)
        checked = conversation_ingest.preview(db, project_id, values, source)
        if checked['fingerprint'] != payload.fingerprint:
            raise HTTPException(409, 'The conversation or existing import changed. Check the source again before saving.')
        existing = checked['existing']
        if existing and existing['duplicate']:
            raise HTTPException(409, 'This conversation is already saved in this project.')
        if existing and not payload.replace_existing:
            raise HTTPException(409, 'Confirm updating the existing conversation before saving.')
        if checked['completeness'] == 'partial' and not payload.confirm_partial:
            raise HTTPException(422, 'Confirm that you want to import a partial conversation.')
        if existing:
            note = scoped(db, Note, project_id, existing['id'])
            before = serialize(note)
            if note.conversation is None:
                # Retain the pre-existing source when upgrading a legacy share.
                from hashlib import sha256
                original = note.content.encode()
                db.add(ConversationImportSource(project_id=project_id, note_id=note.id,
                    filename='legacy-conversation.txt', reference=note.reference, payload=original,
                    content_hash=sha256(original).hexdigest()))
            conversation_ingest.preserve_source_identity(values, note.reference, note.conversation)
            apply_values(note, values)
        else:
            note = Note(project_id=project_id, **values)
            db.add(note)
            before = None
        db.flush()
        db.add(ConversationImportSource(project_id=project_id, note_id=note.id, **source))
        audit(db, project_id, note, 'updated' if existing else 'imported', before)
        db.commit()
        return serialize(note)

    @app.get('/api/projects/{project_id}/conversations/{note_id}/sources')
    def conversation_sources(project_id: int, note_id: int, db: Session = db_dep):
        scoped(db, Note, project_id, note_id)
        source = ConversationImportSource
        rows = db.execute(select(source.id, source.filename, source.reference, source.content_hash, source.created_at)
            .where(source.project_id == project_id, source.note_id == note_id).order_by(source.id.desc())).mappings()
        return [dict(row) for row in rows]

    @app.get('/api/projects/{project_id}/conversations/{note_id}/sources/{source_id}')
    def download_conversation_source(project_id: int, note_id: int, source_id: int, db: Session = db_dep):
        scoped(db, Note, project_id, note_id)
        source = db.scalar(select(ConversationImportSource).where(ConversationImportSource.project_id == project_id,
            ConversationImportSource.note_id == note_id, ConversationImportSource.id == source_id))
        if source is None:
            raise HTTPException(404, 'Source copy not found in this conversation.')
        return Response(source.payload, media_type='application/octet-stream', headers={
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(source.filename, safe=''),
            'X-Content-Type-Options': 'nosniff',
        })

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
    def get_audit(project_id:int,db:Session=db_dep,
                  offset: int = Query(0, ge=0), limit: int | None = Query(None, ge=1, le=1000)):
        return audit_response(db, project_id, offset, limit)

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
        return project_export_response(db, project_id)

    @app.get('/api/projects/{project_id}/export.zip')
    def export_zip(project_id: int, db: Session = db_dep):
        return project_archive_response(db, project_id)

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
    except ModuleNotFoundError as exc:
        if exc.name not in ('backend.app.integrations','backend.app.integrations.codex','backend.app.integrations.codex.router'):raise

    frontend = runtime_resource('frontend', 'frontend', 'dist')
    if frontend.is_dir():
        if (frontend/'assets').is_dir():app.mount('/assets',StaticFiles(directory=frontend/'assets'),name='assets')
        @app.get('/{path:path}')
        def web(path:str):
            if path.startswith('api/'):raise HTTPException(404,'API endpoint not found')
            return FileResponse(frontend/'index.html')
    return app


def apply_values(obj,values):
    nullable={c.name for c in obj.__table__.columns if c.nullable}
    for key,value in values.items():
        if value is None and key not in nullable:raise HTTPException(422,f'{key} cannot be null')
        setattr(obj,key,value)


def make_task(db,project_id,decision,values):
    t=Task(project_id=project_id,title=values.get('title') or 'Implement: '+decision.title,description=values.get('description') if values.get('description') is not None else decision.description,priority=values.get('priority','medium'),status='ready',question_id=decision.question_id,decision_id=decision.id)
    db.add(t);audit(db,project_id,t,'created_from_decision');return t
