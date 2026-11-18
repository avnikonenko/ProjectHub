import hashlib
import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app


@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path/'data')
    with TestClient(app) as c:yield c
    app.state.engine.dispose()


def project(c,name='Research',**kwargs):
    response=c.post('/api/projects',json={'name':name,**kwargs});assert response.status_code==201,response.text;return response.json()


def question(c,pid,title='Which method?'):
    r=c.post(f'/api/projects/{pid}/questions',json={'title':title});assert r.status_code==201,r.text;return r.json()


def answer(c,pid,qid,solution='Method A',**kwargs):
    r=c.post(f'/api/projects/{pid}/questions/{qid}/answers',json={'solution':solution,**kwargs});assert r.status_code==201,r.text;return r.json()


def test_project_git_remotes_redact_new_and_legacy_credentials(client):
    from sqlalchemy.orm import Session
    from backend.app.models import AuditEvent, Project

    unsafe = 'https://remote-user:fake-pass@github.com/team/sample.git?token=fake-query#fake-fragment'
    safe = 'https://github.com/team/sample.git'
    saved = project(client, git_remote=unsafe)
    pid = saved['id']
    route = f'/api/projects/{pid}'
    assert saved['git_remote'] == safe
    with Session(client.app.state.engine) as db:
        assert db.get(Project, pid).git_remote == safe
    response = client.patch(route, json={'git_remote': 'git@github.com:team/other.git'})
    assert response.status_code == 200
    assert response.json()['git_remote'] == 'https://github.com/team/other'
    cleared = client.patch(route, json={'git_remote': '  '})
    assert cleared.status_code == 200 and cleared.json()['git_remote'] == ''

    # Restore the shape of a pre-existing, unsanitized record in this test DB.
    with Session(client.app.state.engine) as db:
        db.get(Project, pid).git_remote = unsafe
        db.add(AuditEvent(project_id=pid, entity_type='projects', entity_id=pid,
                          action='updated', before={'git_remote': unsafe}, after={'git_remote': unsafe}))
        db.commit()
    for suffix in ('', '/overview?compact=true&note_content=false', '/audit', '/export'):
        response = client.get(route + suffix)
        assert response.status_code == 200
        assert 'remote-user' not in response.text and 'fake-' not in response.text
        assert safe in response.text
    with Session(client.app.state.engine) as db:
        assert db.get(Project, pid).git_remote == unsafe


@pytest.mark.parametrize(('remote', 'expected'), [
    ('git://example.org/team/repo.git', 'git://example.org/team/repo.git'),
    ('https://user:dummy@example.org/Team%20Name/_git/Repo?token=dummy',
     'https://example.org/Team%20Name/_git/Repo'),
])
def test_project_git_remotes_preserve_safe_git_and_escaped_paths(client, remote, expected):
    row = project(client, git_remote=remote)
    route = f"/api/projects/{row['id']}"
    assert row['git_remote'] == expected
    assert client.get(route).json()['git_remote'] == expected
    assert expected in client.get(f'{route}/export').text


@pytest.mark.parametrize('path', ('team%2fother/repo', 'team/%2e%2e/repo', 'team/%2520', 'team/%0a'))
def test_project_git_remotes_reject_encoded_separators_and_controls(client, path):
    response = client.post('/api/projects', json={'name': 'test', 'git_remote': f'https://example.org/{path}'})
    assert response.status_code == 422


def test_compact_workspace_reads_preserve_full_api_and_project_scope(client):
    first = project(client, 'First', description='Full project metadata')
    second = project(client, 'Second')
    project_id, other_id = first['id'], second['id']
    question(client, project_id)
    for pid in (project_id, other_id):
        assert client.post(f'/api/projects/{pid}/notes', json={'title': f'Note {pid}'}).status_code == 201
        assert client.post(f'/api/projects/{pid}/tasks', json={'title': f'Task {pid}'}).status_code == 201

    full_projects = client.get('/api/projects').json()
    assert [item['id'] for item in full_projects] == [project_id, other_id]
    assert full_projects[0]['description'] == 'Full project metadata'
    assert list(full_projects[0]) == list(first)
    assert client.get('/api/projects?compact=true').json() == [
        {'id': project_id, 'name': 'First', 'pinned': False}, {'id': other_id, 'name': 'Second', 'pinned': False},
    ]
    path = f'/api/projects/{project_id}/overview'
    full = client.get(path).json()
    assert list(full) == ['project', 'questions', 'decisions', 'tasks', 'notes', 'sessions', 'activity']
    assert len(full['questions']) == 1
    compact = client.get(f'{path}?compact=true').json()
    assert compact == {key: full[key] for key in ('project', 'tasks', 'notes', 'activity')}
    assert [note['title'] for note in compact['notes']] == [f'Note {project_id}']
    assert [task['title'] for task in compact['tasks']] == [f'Task {project_id}']
    assert all(event['project_id'] == project_id for event in compact['activity'])
    assert client.get('/api/projects/99999/overview?compact=true').status_code == 404


def test_accept_reopen_provenance_and_task(client):
    p=project(client);pid=p['id'];q=question(client,pid);qid=q['id']
    a=answer(client,pid,qid,evidence='Paper DOI:example',explanation='Validated experimentally')
    b=answer(client,pid,qid,'Method B')
    accepted=client.post(f'/api/projects/{pid}/questions/{qid}/accept',json={'answer_id':a['id'],'create_task':True})
    assert accepted.status_code==200,accepted.text
    data=accepted.json();assert data['question']['status']=='resolved';assert data['decision']['status']=='accepted'
    assert data['decision']['alternatives']=='Method B';assert data['task']['decision_id']==data['decision']['id']
    again=client.post(f'/api/projects/{pid}/questions/{qid}/accept',json={'answer_id':a['id'],'create_task':True}).json()
    assert again['decision']['id']==data['decision']['id'];assert again['task']['id']==data['task']['id']
    changed=client.post(f'/api/projects/{pid}/questions/{qid}/accept',json={'answer_id':b['id']}).json()
    assert changed['decision']['id']!=data['decision']['id']
    decisions=client.get(f'/api/projects/{pid}/decisions').json();assert [d['status'] for d in decisions]==['superseded','accepted']
    reopened=client.post(f'/api/projects/{pid}/questions/{qid}/reopen').json();assert reopened['status']=='reopened';assert reopened['accepted_answer_id'] is None
    assert all(d['status']=='superseded' for d in client.get(f'/api/projects/{pid}/decisions').json())
    events=client.get(f'/api/projects/{pid}/audit').json();assert any(e['action']=='reopened' and e['before']['accepted_answer_id']==b['id'] for e in events)
    assert any(e['action']=='accepted_from_answer' for e in events)


def test_decision_next_step_without_creating_task(client):
    pid=project(client)['id'];qid=question(client,pid)['id']
    aid=answer(client,pid,qid)['id']
    url=f'/api/projects/{pid}'
    accepted=client.post(f'{url}/questions/{qid}/accept',json={'answer_id':aid,'next_step':'Run the control'})
    assert accepted.status_code==200,accepted.text
    data=accepted.json()
    assert data['task'] is None
    assert data['decision']['next_step']=='Run the control'
    did=data['decision']['id']
    revised=client.patch(f'{url}/decisions/{did}',json={'next_step':'Repeat the control'})
    assert revised.status_code==200,revised.text
    assert client.get(f'{url}/overview').json()['decisions'][0]['next_step']=='Repeat the control'
    events=client.get(f'{url}/audit').json()
    assert any(e['action']=='revised' and e['before']['next_step']=='Run the control' for e in events)
    assert client.get(f'{url}/tasks').json()==[]


def test_wrong_answer_atomic_and_project_isolation(client):
    a=project(client,'A');b=project(client,'B');q=question(client,a['id']);other=question(client,b['id']);ans=answer(client,b['id'],other['id'])
    assert client.post(f"/api/projects/{a['id']}/questions/{q['id']}/accept",json={'answer_id':ans['id']}).status_code==404
    same_project_question=question(client,a['id']);wrong=answer(client,a['id'],same_project_question['id'])
    assert client.post(f"/api/projects/{a['id']}/questions/{q['id']}/accept",json={'answer_id':wrong['id']}).status_code==422
    state=client.get(f"/api/projects/{a['id']}/overview").json();assert state['decisions']==[];assert state['questions'][0]['status']=='open'
    for resource in ('overview','export','summary','review','audit'):
        assert client.get(f'/api/projects/99999/{resource}').status_code==404
    assert client.patch(f"/api/projects/{b['id']}/questions/{q['id']}",json={'title':'Hijack'}).status_code==404
    assert client.post(f"/api/projects/{a['id']}/tasks",json={'title':'Bad','question_id':other['id']}).status_code==404
    export=client.get(f"/api/projects/{a['id']}/export").json();assert export['answers'][0]['id']==wrong['id'];assert all(x['project_id']==a['id'] for x in export['questions'])


def test_existing_repo_registration_does_not_modify_files(client,tmp_path):
    repo=tmp_path/'repo';repo.mkdir();subprocess.run(['git','init',str(repo)],check=True,capture_output=True)
    (repo/'source.py').write_text('print(42)\n')
    def snapshot():return {str(p.relative_to(repo)):hashlib.sha256(p.read_bytes()).hexdigest() for p in repo.rglob('*') if p.is_file()}
    before=snapshot();p=project(client,repository_path=str(repo));assert p['repository_path']==str(repo);assert snapshot()==before
    for unsafe in [str(repo/'..'/'repo'),'relative/path','/',str(tmp_path/'missing')]:
        assert client.post('/api/projects',json={'name':'Unsafe','repository_path':unsafe}).status_code==422
    link=tmp_path/'link';link.symlink_to(repo,target_is_directory=True)
    assert client.post('/api/projects',json={'name':'Unsafe','repository_path':str(link)}).status_code==422
    assert client.delete(f"/api/projects/{p['id']}").status_code == 204
    assert snapshot() == before


def test_persistence_summary_search_export_backup(tmp_path):
    data_dir=tmp_path/'data';app=create_app(data_dir)
    with TestClient(app) as c:
        p=project(c,goals='Reproducible ligand preparation');q=question(c,p['id'],'Validate protonation')
        c.post(f"/api/projects/{p['id']}/notes",json={'title':'Evidence','content':'Protonation benchmark','kind':'evidence'})
        assert c.get(f"/api/projects/{p['id']}/search",params={'q':'protonation'}).json()
        assert c.get(f"/api/projects/{p['id']}/search",params={'q':'\" OR * (x)'}).status_code==200
        summary=c.get(f"/api/projects/{p['id']}/summary").json();assert 'Reproducible ligand' in summary['markdown'];assert 'Validate protonation' in summary['markdown']
        review=c.get(f"/api/projects/{p['id']}/review").json();assert review['source']=='deterministic';assert review['findings']==[]
        backup=c.post('/api/backup').json();assert Path(backup['path']).is_file()
        with sqlite3.connect(backup['path']) as db:assert db.execute('SELECT count(*) FROM projects').fetchone()[0]==1
    app.state.engine.dispose()
    app2=create_app(data_dir)
    with TestClient(app2) as c:
        assert c.get('/api/projects').json()[0]['id']==p['id'];assert c.get(f"/api/projects/{p['id']}/overview").json()['questions'][0]['id']==q['id']
    app2.state.engine.dispose()


def test_documents_limits_and_safe_names(client):
    p=project(client);url=f"/api/projects/{p['id']}/documents"
    for filename,body,status in [('../bad.md',b'evil',422),('bad.html',b'html',422),('binary.txt',b'\x00',422),('invalid.txt',b'\xff',422),('large.md',b'x'*(2*1024*1024+1),413)]:
        assert client.post(url,files={'file':(filename,body,'text/plain')}).status_code==status
    r=client.post(url,files={'file':('paper.md',b'# Evidence\nUntrusted text','text/plain')});assert r.status_code==201,r.text
    note=r.json();assert note['kind']=='document';assert '/' not in note['reference'];assert note['content'].startswith('# Evidence')
    assert (client.app.state.data_dir/'uploads'/note['reference']).read_text()==note['content']


def test_invalid_fields_links_and_audited_decision_revision(client):
    p=project(client);pid=p['id']
    for payload in [{'name':''},{'name':'x'*201},{'name':'Fine','surprise':True}]:assert client.post('/api/projects',json=payload).status_code==422
    assert client.patch(f'/api/projects/{pid}',json={'name':None}).status_code==422
    assert client.patch(f'/api/projects/{pid}',json={'name':'x'*201}).status_code==422
    q=question(client,pid);assert client.patch(f"/api/projects/{pid}/questions/{q['id']}",json={'status':'resolved'}).status_code==422
    d=client.post(f'/api/projects/{pid}/decisions',json={'title':'Use A','status':'accepted'}).json()
    revised=client.patch(f"/api/projects/{pid}/decisions/{d['id']}",json={'description':'Revised with explanation'});assert revised.status_code==200,revised.text
    events=client.get(f'/api/projects/{pid}/audit').json();revision=[e for e in events if e['action']=='revised'][0];assert revision['before']['description']=='';assert revision['after']['description']=='Revised with explanation'
    task=client.post(f"/api/projects/{pid}/decisions/{d['id']}/tasks",json={});assert task.status_code==201
    assert client.patch(f"/api/projects/{pid}/tasks/{task.json()['id']}",json={'status':'completed'}).status_code==200


def test_no_api_key_required_and_origin_host_protection(client,monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    assert client.get('/api/health').json()['status']=='ok';assert project(client)['name']=='Research'
    assert client.post('/api/projects',json={'name':'Attack'},headers={'Origin':'https://evil.example'}).status_code==403
    assert client.get('/api/projects',headers={'Host':'evil.example'}).status_code==403
    assert client.post('/api/projects',json={'name':'Attack'},headers={'Sec-Fetch-Site':'cross-site'}).status_code==403


def test_concurrent_acceptance_single_current_decision(client):
    p=project(client);pid=p['id'];q=question(client,pid);a=answer(client,pid,q['id']);b=answer(client,pid,q['id'],'B')
    def accept(aid):return client.post(f"/api/projects/{pid}/questions/{q['id']}/accept",json={'answer_id':aid})
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(accept,[a['id'],b['id']]))
    assert all(r.status_code==200 for r in responses),[r.text for r in responses]
    decisions=client.get(f'/api/projects/{pid}/decisions').json();assert sum(d['status']=='accepted' for d in decisions)==1
    current=client.get(f'/api/projects/{pid}/overview').json()['questions'][0]
    accepted=[a for a in current['answers'] if a['status']=='accepted'];assert len(accepted)==1;assert accepted[0]['id']==current['accepted_answer_id']


def test_question_patch_serializes_with_acceptance(client,monkeypatch):
    import backend.app.main as main
    p=project(client);pid=p['id'];q=question(client,pid);a=answer(client,pid,q['id'])
    url=f"/api/projects/{pid}/questions/{q['id']}"
    checked,release=Event(),Event()
    original=main.apply_values

    def pause_after_state_check(obj,values):
        if isinstance(obj,main.Question):
            checked.set()
            assert release.wait(10),'Timed out waiting to finish question update'
        original(obj,values)

    monkeypatch.setattr(main,'apply_values',pause_after_state_check)
    with ThreadPoolExecutor(max_workers=2) as pool:
        patch=pool.submit(client.patch,url,json={'status':'under_discussion'})
        try:
            assert checked.wait(10),'Question update did not reach state check'
            # A second writer must not enter between validation and mutation.
            # timeout=0 makes this check deterministic without scheduler sleeps.
            with sqlite3.connect(client.app.state.data_dir/'projecthub.sqlite3',timeout=0) as contender:
                with pytest.raises(sqlite3.OperationalError,match='locked'):
                    contender.execute('BEGIN IMMEDIATE')
            accept=pool.submit(client.post,url+'/accept',json={'answer_id':a['id']})
        finally:
            release.set()
        assert patch.result(timeout=10).status_code==200
        assert accept.result(timeout=10).status_code==200
    current=client.get(f'/api/projects/{pid}/questions').json()[0]
    assert current['status']=='resolved'
    assert current['accepted_answer_id']==a['id']
    assert client.patch(url,json={'status':'open'}).status_code==422


def test_import_chatgpt_share(client, monkeypatch):
    from backend.app.integrations import chatgpt_share

    url = 'https://chatgpt.com/share/01234567-89ab-cdef-0123-456789abcdef'
    calls = []

    def read(shared_url):
        calls.append(shared_url)
        return 'Database choice', 'User:\nWhich database?\n\nChatGPT:\nUse SQLite.'

    monkeypatch.setattr(chatgpt_share, 'read_transcript', read)
    pid = project(client)['id']
    other = project(client, 'Other')['id']
    path = f'/api/projects/{pid}/notes/chatgpt-share'
    assert client.post('/api/projects/99999/notes/chatgpt-share', json={'url': url}).status_code == 404
    assert client.post(path, json={'url': url.replace('/share/', '/c/')}).status_code == 422
    imported = client.post(path, json={'url': f'{url}?utm_source=chatgpt'})
    assert imported.status_code == 201, imported.text
    note = imported.json()
    assert (note['reference'], note['kind'], note['title']) == (url, 'document', 'Database choice')
    assert 'Use SQLite.' in note['content']
    assert client.post(path, json={'url': url}).status_code == 409
    assert calls == [url]
    assert client.get(f'/api/projects/{pid}/overview').json()['notes'] == [note]
    assert client.get(f'/api/projects/{other}/notes').json() == []

    def unavailable(_):
        raise chatgpt_share.ShareReadError('No readable transcript')

    monkeypatch.setattr(chatgpt_share, 'read_transcript', unavailable)
    assert client.post(f'/api/projects/{other}/notes/chatgpt-share', json={'url': url}).status_code == 502
    assert client.get(f'/api/projects/{other}/notes').json() == []


def test_bulk_tasks_validate_atomically_and_preserve_order(client):
    pid = project(client)['id']
    other = project(client, 'Other')['id']
    foreign_question = question(client, other)['id']
    path = f'/api/projects/{pid}/tasks/bulk'
    before = client.get(f'/api/projects/{pid}/audit').json()
    response = client.post(path, json={'items': [
        {'title': 'Valid first'}, {'title': 'Foreign', 'question_id': foreign_question},
    ]})
    assert response.status_code == 404
    assert client.get(f'/api/projects/{pid}/tasks').json() == []
    assert client.get(f'/api/projects/{pid}/audit').json() == before
    for items in ([], [{'title': 'A'}] * 101, [{'title': 'Valid'}, {'title': ' ' }]):
        assert client.post(path, json={'items': items}).status_code == 422
    assert client.get(f'/api/projects/{pid}/tasks').json() == []
    content = 'Detailed follow-up\n' * 500
    response = client.post(path, json={'items': [
        {'title': 'First', 'description': content}, {'title': 'Second'},
    ]})
    assert response.status_code == 201
    tasks = response.json()
    assert [t['title'] for t in tasks] == ['First', 'Second']
    assert tasks[0]['description'] == content.strip()
    events = client.get(f'/api/projects/{pid}/audit').json()
    assert sum(e['entity_type'] == 'tasks' and e['action'] == 'created' for e in events) == 2
    assert client.post('/api/projects/99999/tasks/bulk', json={'items': [{'title': 'No'}]}).status_code == 404


def test_delete_task_scoped_audited_and_removes_search_entry(client):
    pid = project(client)['id']
    other = project(client, 'Other')['id']
    decision = client.post(f'/api/projects/{pid}/decisions', json={
        'title': 'Keep the reason', 'status': 'accepted', 'rationale': 'Durable choice',
    }).json()
    task = client.post(f'/api/projects/{pid}/tasks', json={
        'title': 'Removableneedle', 'decision_id': decision['id'], 'description': 'Details',
    }).json()
    path = f"/api/projects/{pid}/tasks/{task['id']}"
    assert client.delete(f"/api/projects/{other}/tasks/{task['id']}").status_code == 404
    assert client.get(f'/api/projects/{pid}/search', params={'q': 'Removableneedle'}).json()
    deleted = client.delete(path)
    assert deleted.status_code == 204 and deleted.content == b''
    assert client.get(f'/api/projects/{pid}/tasks').json() == []
    assert client.get(f'/api/projects/{pid}/search', params={'q': 'Removableneedle'}).json() == []
    assert client.get(f'/api/projects/{pid}/decisions').json()[0] == decision
    events = client.get(f'/api/projects/{pid}/audit').json()
    history = [e for e in events if e['entity_type'] == 'tasks' and e['entity_id'] == task['id']]
    assert [e['action'] for e in history] == ['created', 'deleted']
    assert history[-1]['before'] == task
    assert client.delete(path).status_code == 404


def test_delete_note_is_scoped_and_preserves_uploaded_file(client):
    pid = project(client)['id']
    other = project(client, 'Other')['id']
    base = f'/api/projects/{pid}'
    content = b'Deletablenote content'
    imported = client.post(f'{base}/documents', files={'file': ('example.txt', content, 'text/plain')})
    assert imported.status_code == 201, imported.text
    note = imported.json()
    uploaded = client.app.state.data_dir / 'uploads' / note['reference']
    path = f"{base}/notes/{note['id']}"
    assert client.delete(f"/api/projects/{other}/notes/{note['id']}").status_code == 404
    assert client.get(f'{base}/search', params={'q': 'Deletablenote'}).json()
    deleted = client.delete(path)
    assert deleted.status_code == 204 and deleted.content == b''
    assert client.get(f'{base}/notes').json() == []
    assert client.get(f'{base}/search', params={'q': 'Deletablenote'}).json() == []
    assert uploaded.read_bytes() == content
    events = client.get(f'{base}/audit').json()
    history = [e for e in events if e['entity_type'] == 'notes' and e['entity_id'] == note['id']]
    assert [e['action'] for e in history] == ['imported', 'deleted']
    assert history[-1]['before'] == note
    assert client.delete(path).status_code == 404


def test_review_checks_todos_and_notes_without_structured_workflow_findings(client):
    pid = project(client)['id']
    other = project(client, 'Other')['id']
    base = f'/api/projects/{pid}'
    legacy_question = question(client, pid, 'Historical open question')
    historical_answer = answer(client, pid, legacy_question['id'], 'Historical answer')
    for _ in range(2):
        response = client.post(f'{base}/decisions', json={
            'title': 'Historical choice', 'status': 'accepted',
            'limitations': 'Historical decision limitation',
        })
        assert response.status_code == 201
    task = client.post(f'{base}/tasks', json={'title': 'Check the draft'}).json()
    client.post(f'{base}/tasks', json={'title': 'Finished work', 'status': 'completed'})
    note = client.post(f'{base}/notes', json={
        'title': 'Known limit', 'kind': 'limitation', 'content': 'The import handles text only.',
    }).json()
    client.post(f'/api/projects/{other}/tasks', json={'title': 'Private task'})
    client.post(f'/api/projects/{other}/notes', json={
        'title': 'Private limit', 'kind': 'limitation', 'content': 'Private limitation',
    })
    before = client.get(f'{base}/export').json()

    response = client.get(f'{base}/review')
    assert response.status_code == 200
    report = response.json()
    assert report['project_id'] == pid and report['source'] == 'deterministic'
    assert [(finding['category'], finding['entity_id']) for finding in report['findings']] == [
        ('incomplete_task', task['id']), ('limitation', note['id']),
    ]
    assert all(finding['status'] == 'unverified' for finding in report['findings'])
    assert report['limitations']
    after = client.get(f'{base}/export').json()
    for field in ('questions', 'answers', 'decisions', 'audit'):
        assert after[field] == before[field]
    assert after['questions'][0]['id'] == legacy_question['id']
    assert after['answers'][0]['id'] == historical_answer['id']
    assert len(after['decisions']) == 2
    assert 'Historical open question' in client.get(f'{base}/summary').json()['markdown']


def test_repository_snapshot_is_scoped_and_read_only(client, tmp_path, monkeypatch):
    repo = tmp_path / 'git-example'
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    repo.mkdir()
    git('init', '-b', 'work')
    p = project(client, repository_path=str(repo))
    url = f"/api/projects/{p['id']}/repository"
    assert client.get(url).json() == {'branch': 'work', 'dirty': False, 'commits': [],
                                     'tracked_files': 0, 'tracked_changes': 0, 'untracked_files': 0,
                                     'upstream': None, 'github_issues_url': None,
                                     'remote_repository': None,
                                     'branches': ['work'], 'branches_limited': False,
                                     'remote_branches': [], 'remote_branches_limited': False}
    assert client.get(f"/api/projects/{project(client, 'Other')['id']}/repository").status_code == 404
    assert client.get('/api/projects/99999/repository').status_code == 404

    git('config', 'user.name', 'Example')
    git('config', 'user.email', 'example@example.test')
    (repo / '.gitattributes').write_text('file.txt filter=custom\n')
    (repo / 'file.txt').write_text('original\n')
    git('add', '.')
    for i in range(4):
        monkeypatch.setenv('GIT_COMMITTER_DATE', f'2025-01-02T03:04:0{i}+05:30')
        git('commit', '--allow-empty', '-m', f'Change {i}')
    assert client.get(url).json()['dirty'] is False
    git('checkout', '--detach', 'HEAD')
    git('config', 'filter.custom.clean', 'touch ran-filter; cat')
    git('config', 'core.fsmonitor', 'touch ran-monitor')
    (repo / 'file.txt').write_text('modified\n')
    index = (repo / '.git/index').read_bytes()
    monkeypatch.setenv('GIT_DIR', str(tmp_path / 'not-a-repository'))
    response = client.get(url)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['branch'] is None and result['dirty'] is True
    assert result['branches'] == ['work'] and result['branches_limited'] is False
    assert result['tracked_files'] == 2 and result['tracked_changes'] == 1 and result['untracked_files'] == 0
    assert result['upstream'] is None
    assert [commit['subject'] for commit in result['commits']] == ['Change 3', 'Change 2', 'Change 1']
    assert [commit['committed_at'] for commit in result['commits']] == [
        '2025-01-02T03:04:03+05:30', '2025-01-02T03:04:02+05:30', '2025-01-02T03:04:01+05:30',
    ]
    assert not (repo / 'ran-filter').exists()
    assert not (repo / 'ran-monitor').exists()
    assert (repo / '.git/index').read_bytes() == index


def test_repository_snapshot_counts_tracked_and_untracked_files(client, tmp_path):
    repo = tmp_path / 'counts'
    repo.mkdir()

    def git(*args):
        subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)

    git('init', '-b', 'work')
    git('config', 'user.name', 'Example')
    git('config', 'user.email', 'example@example.test')
    (repo / 'tracked.txt').write_text('original\n')
    (repo / 'rename.txt').write_text('rename me\n')
    git('add', '.')
    git('commit', '-m', 'Initial')
    linked = project(client, repository_path=str(repo))
    url = f"/api/projects/{linked['id']}/repository"

    def counts(tracked, untracked, total=2):
        response = client.get(url)
        assert response.status_code == 200, response.text
        status = response.json()
        assert status['tracked_changes'] == tracked
        assert status['untracked_files'] == untracked
        assert status['tracked_files'] == total
        assert status['dirty'] is bool(tracked or untracked)

    counts(0, 0)
    (repo / 'new directory').mkdir()
    (repo / 'new directory' / 'one.txt').write_text('untracked\n')
    (repo / 'new directory' / 'two\nlines.txt').write_text('untracked\n')
    counts(0, 2)
    (repo / 'tracked.txt').write_text('staged\n')
    git('add', 'tracked.txt')
    (repo / 'tracked.txt').write_text('staged and unstaged\n')
    counts(1, 2)
    git('mv', 'rename.txt', 'renamed\nfile.txt')
    counts(2, 2)
    git('add', 'new directory')
    counts(4, 0, total=4)


def test_repository_snapshot_compares_saved_upstream_tips(client, tmp_path, monkeypatch):
    repo = tmp_path / 'upstream'
    repo.mkdir()

    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(subject, date, *parents):
        monkeypatch.setenv('GIT_COMMITTER_DATE', date)
        return git('commit-tree', 'HEAD^{tree}', *parents, '-m', subject)

    git('init', '-b', 'main')
    git('config', 'user.name', 'Example')
    git('config', 'user.email', 'example@example.test')
    git('commit', '--allow-empty', '-m', 'Initial')
    base = git('rev-parse', 'HEAD')
    git('remote', 'add', 'origin', str(tmp_path / 'not-accessible'))
    project_id = project(client, repository_path=str(repo))['id']
    url = f'/api/projects/{project_id}/repository'
    assert client.get(url).json()['upstream'] is None
    git('config', 'branch.main.remote', 'origin')
    git('config', 'branch.main.merge', 'refs/heads/main')
    expected = {'branch': 'origin/main', 'is_remote': True, 'ahead': None, 'behind': None, 'commit': None}
    assert client.get(url).json()['upstream'] == expected

    git('update-ref', 'refs/remotes/origin/main', base)
    snapshot = client.get(url).json()
    assert (snapshot['upstream']['ahead'], snapshot['upstream']['behind']) == (0, 0)
    assert snapshot['upstream']['commit']['hash'] == snapshot['commits'][0]['hash']
    remote_date = '2025-02-03T04:05:06+05:30'
    remote_tip = commit('Remote work', remote_date, '-p', base)
    git('update-ref', 'refs/remotes/origin/main', remote_tip)
    snapshot = client.get(url).json()
    assert (snapshot['upstream']['ahead'], snapshot['upstream']['behind']) == (0, 1)
    assert snapshot['upstream']['commit'] == {'hash': git('rev-parse', '--short', remote_tip), 'committed_at': remote_date}
    local_tip = commit('Local work', '2025-02-04T04:05:06+05:30', '-p', base)
    git('update-ref', 'refs/heads/main', local_tip)
    snapshot = client.get(url).json()
    assert (snapshot['upstream']['ahead'], snapshot['upstream']['behind']) == (1, 1)
    assert snapshot['commits'][0]['committed_at'] == '2025-02-04T04:05:06+05:30'
    git('update-ref', 'refs/remotes/origin/main', base)
    snapshot = client.get(url).json()
    assert (snapshot['upstream']['ahead'], snapshot['upstream']['behind']) == (1, 0)

    (repo / '.git/shallow').write_text(base + '\n')
    upstream = client.get(url).json()['upstream']
    assert upstream['commit'] is not None and upstream['ahead'] is None and upstream['behind'] is None


def test_repository_snapshot_reports_tracking_before_the_first_local_commit(client, tmp_path):
    repo = tmp_path / 'unborn-upstream'
    repo.mkdir()

    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    git('init', '-b', 'main')
    git('remote', 'add', 'team', 'https://example.test/team/project.git')
    git('config', 'branch.main.remote', 'team')
    git('config', 'branch.main.merge', 'refs/heads/main')
    url = f"/api/projects/{project(client, repository_path=str(repo))['id']}/repository"
    snapshot = client.get(url).json()
    assert snapshot['branch'] == 'main' and snapshot['commits'] == []
    assert snapshot['upstream'] == {'branch': 'team/main', 'is_remote': True, 'ahead': None, 'behind': None, 'commit': None}
    assert snapshot['remote_repository']['name'] == 'team'

    git('-c', 'user.name=Example', '-c', 'user.email=example@example.test', 'commit', '--allow-empty', '-m', 'Initial')
    git('update-ref', 'refs/remotes/team/main', 'HEAD')
    git('checkout', '--orphan', 'new')
    git('config', 'branch.new.remote', 'team')
    git('config', 'branch.new.merge', 'refs/heads/main')
    snapshot = client.get(url).json()
    assert snapshot['branch'] == 'new' and snapshot['commits'] == []
    upstream = snapshot['upstream']
    assert upstream['branch'] == 'team/main' and upstream['commit']['hash'] == git('rev-parse', '--short', 'refs/remotes/team/main')
    assert upstream['ahead'] is None and upstream['behind'] is None


def test_repository_snapshot_pins_tip_to_the_captured_branch(client, tmp_path, monkeypatch):
    from backend.app import project_evidence

    repo = tmp_path / 'switched-head'
    repo.mkdir()

    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    git('init', '-b', 'main')
    git('config', 'user.name', 'Example')
    git('config', 'user.email', 'example@example.test')
    git('commit', '--allow-empty', '-m', 'Main')
    git('remote', 'add', 'origin', 'https://example.test/team/project.git')
    git('update-ref', 'refs/remotes/origin/main', 'HEAD')
    git('branch', '--set-upstream-to=origin/main')
    git('checkout', '-b', 'other')
    git('commit', '--allow-empty', '-m', 'Other')
    git('checkout', 'main')
    url = f"/api/projects/{project(client, repository_path=str(repo))['id']}/repository"
    original_git = project_evidence._git

    def switch_after_reading_branch(path, *args, **kwargs):
        result = original_git(path, *args, **kwargs)
        if args == ('symbolic-ref', '--quiet', 'HEAD'):
            git('symbolic-ref', 'HEAD', 'refs/heads/other')
        return result

    monkeypatch.setattr(project_evidence, '_git', switch_after_reading_branch)
    snapshot = client.get(url).json()
    assert snapshot['branch'] == 'main' and snapshot['commits'][0]['subject'] == 'Main'
    assert snapshot['upstream']['ahead'] == snapshot['upstream']['behind'] == 0


def test_repository_snapshot_deduplicates_conflicted_index_paths(client, tmp_path):
    repo = tmp_path / 'conflicted-index'
    repo.mkdir()

    def git(*args, input=None):
        return subprocess.run(['git', '-C', str(repo), *args], input=input, check=True,
                              capture_output=True).stdout.strip()

    git('init', '-b', 'main')
    blob = git('hash-object', '-w', '--stdin', input=b'file contents\n')
    conflict = b'with\na-newline'
    paths = [(0, b'invalid-utf8-\xff'), (0, b'invalid-utf8-\xfe'), (2, conflict), (3, conflict)]
    git('update-index', '-z', '--index-info', input=b''.join(
        b'100644 ' + blob + b' ' + str(stage).encode() + b'\t' + path + b'\0' for stage, path in paths))
    project_id = project(client, repository_path=str(repo))['id']
    response = client.get(f'/api/projects/{project_id}/repository')
    assert response.status_code == 200, response.text
    assert response.json()['tracked_files'] == 3


def test_repository_snapshot_sanitizes_remote_addresses_and_prefers_the_upstream(client, tmp_path):
    repo = tmp_path / 'remote-addresses'
    repo.mkdir()

    def git(*args):
        subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)

    git('init', '-b', 'main')
    git('-c', 'user.name=Example', '-c', 'user.email=example@example.test', 'commit', '--allow-empty', '-m', 'Initial')
    git('remote', 'add', 'origin', 'https://user:hidden-password@github.com/owner/repo.git?token=hidden-query#hidden-fragment')
    git('remote', 'add', 'upstream', 'hidden-login@git.example.com:team/repo.git')
    project_id = project(client, repository_path=str(repo), git_remote='https://fallback.example/team/repo.git')['id']
    url = f'/api/projects/{project_id}/repository'

    def remote():
        response = client.get(url)
        assert response.status_code == 200, response.text
        assert 'hidden-' not in response.text
        return response.json()['remote_repository']

    assert remote() == {'name': 'origin', 'address': 'https://github.com/owner/repo.git',
                        'web_url': 'https://github.com/owner/repo'}
    git('config', '--add', 'remote.origin.url', 'https://other.example/mirror.git')
    assert remote() == {'name': 'origin', 'address': 'https://github.com/owner/repo.git',
                        'web_url': 'https://github.com/owner/repo'}
    git('config', '--fixed-value', '--unset-all', 'remote.origin.url', 'https://other.example/mirror.git')
    git('config', 'branch.main.remote', 'upstream')
    git('config', 'branch.main.merge', 'refs/heads/main')
    assert remote() == {'name': 'upstream', 'address': 'git.example.com:team/repo.git'}
    git('remote', 'set-url', 'upstream', 'ssh://hidden-user:hidden-ssh@git.example.com:2222/team/repo.git#hidden-fragment')
    assert remote() == {'name': 'upstream', 'address': 'ssh://git.example.com:2222/team/repo.git'}

    for malformed in ('https://user:hidden-password@host@attacker.example/team/repo', 'https:hidden-token',
                      'https://attacker.example/team/hidden-user%40nested', 'ext::hidden-command'):
        git('remote', 'set-url', 'upstream', malformed)
        git('remote', 'set-url', 'origin', malformed)
        assert remote() == {'name': 'project', 'address': 'https://fallback.example/team/repo.git',
                            'web_url': 'https://fallback.example/team/repo.git'}


def test_repository_snapshot_uses_a_sole_custom_remote_without_tracking(client, tmp_path):
    repo = tmp_path / 'sole-remote'
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'team',
                    'ssh://hidden-user@git.example.test/code.git'], check=True)
    url = f"/api/projects/{project(client, repository_path=str(repo))['id']}/repository"
    assert client.get(url).json()['remote_repository'] == {
        'name': 'team', 'address': 'ssh://git.example.test/code.git',
    }
    subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'other',
                    'https://example.test/code.git'], check=True)
    assert client.get(url).json()['remote_repository'] is None


def test_repository_issues_are_project_scoped_and_only_fetch_on_request(client, tmp_path, monkeypatch):
    from backend.app.integrations import github_issues

    repo = tmp_path / 'project'
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'origin',
                    'git@github.com:local-owner/sample.git'], check=True)
    linked = project(client, repository_path=str(repo))
    explicit = project(client, git_remote='https://github.com/other-owner/another')
    unrelated = project(client, repository_path=str(repo), git_remote='https://example.com/foreign/repo')
    calls = []

    def fetch(repository):
        calls.append(repository)
        return {'url': github_issues.issues_url(repository), 'issues': []}

    monkeypatch.setattr(github_issues, 'open_issues', fetch)
    route = lambda entry: f"/api/projects/{entry['id']}/repository"
    assert client.get(route(linked)).json()['github_issues_url'] == 'https://github.com/local-owner/sample/issues'
    assert client.get(route(unrelated)).json()['github_issues_url'] is None
    assert not calls
    assert client.get(route(linked) + '/issues').status_code == 200
    assert client.get(route(explicit) + '/issues').status_code == 200
    assert client.get(route(unrelated) + '/issues').status_code == 404
    assert client.get('/api/projects/99999/repository/issues').status_code == 404
    assert calls == ['local-owner/sample', 'other-owner/another']


def test_pinned_tasks_are_persistent_scoped_and_sorted_before_paging(client):
    pid = project(client)['id']
    other = project(client, 'Other')['id']
    base = f'/api/projects/{pid}'
    tasks = [client.post(f'{base}/tasks', json={'title': f'Task {i}'}).json() for i in range(12)]
    assert all(task['pinned'] is False for task in tasks)
    target = tasks[0]['id']
    assert client.patch(f'/api/projects/{other}/tasks/{target}', json={'pinned': True}).status_code == 404
    assert client.patch(f'{base}/tasks/{target}', json={'pinned': None}).status_code == 422
    assert client.patch(f'{base}/tasks/{target}', json={'pinned': True}).json()['pinned'] is True
    for path in (f'{base}/tasks', f'{base}/overview?compact=true'):
        data = client.get(path).json()
        rows = data if isinstance(data, list) else data['tasks']
        assert rows[0]['id'] == target and rows[0]['pinned'] is True
    assert client.patch(f'{base}/tasks/{target}', json={'title': 'Edited'}).json()['pinned'] is True
    assert client.patch(f'{base}/tasks/{target}', json={'pinned': False}).json()['pinned'] is False


def test_task_pin_migration_preserves_legacy_tasks(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    engine = create_engine('sqlite:///' + str(tmp_path / 'legacy.sqlite3'))
    cfg = Config()
    cfg.set_main_option('script_location', str(Path(__file__).resolve().parents[2] / 'migrations'))
    with engine.begin() as connection:
        cfg.attributes['connection'] = connection
        command.upgrade(cfg, '0017')
        connection.execute(text("INSERT INTO projects (id,name,description,goals,status,tags,architecture_mermaid,git_remote,created_at,updated_at) VALUES (1,'Legacy','','','planning','[]','','','now','now')"))
        connection.execute(text("INSERT INTO tasks (project_id,title,description,priority,status,created_at,updated_at) VALUES (1,'Keep me','','medium','backlog','now','now')"))
        command.upgrade(cfg, 'head')
        assert connection.execute(text('SELECT title,pinned FROM tasks')).one() == ('Keep me', 0)
    engine.dispose()


def test_not_planned_tasks_are_retained_but_not_recommended(client):
    pid = project(client)['id']
    other = project(client, 'Other')['id']
    base = f'/api/projects/{pid}'
    task = client.post(f'{base}/tasks', json={'title': 'Deferred idea'}).json()
    url = f"{base}/tasks/{task['id']}"
    assert client.patch(f"/api/projects/{other}/tasks/{task['id']}", json={'status': 'not_planned'}).status_code == 404
    result = client.patch(url, json={'status': 'not_planned'})
    assert result.status_code == 200 and result.json()['status'] == 'not_planned'
    assert any(t['id'] == task['id'] and t['status'] == 'not_planned' for t in client.get(f'{base}/tasks').json())
    assert not any(f['entity_id'] == task['id'] for f in client.get(f'{base}/review').json()['findings'])
    assert 'Deferred idea' not in client.get(f'{base}/summary').json()['markdown']
    assert client.patch(url, json={'status': 'backlog'}).status_code == 200
    assert any(f['entity_id'] == task['id'] for f in client.get(f'{base}/review').json()['findings'])


def test_repository_snapshot_lists_local_and_remote_branches_in_worktrees(client, tmp_path):
    repo = tmp_path / 'branches'
    repo.mkdir()

    def git(*args, input=None):
        return subprocess.run(['git', '-C', str(repo), *args], input=input, check=True,
                              capture_output=True, text=True).stdout.strip()

    git('init', '-b', 'main')
    git('config', 'user.name', 'Example')
    git('config', 'user.email', 'example@example.test')
    git('commit', '--allow-empty', '-m', 'Initial')
    git('branch', 'zeta')
    git('branch', 'alpha')
    git('update-ref', 'refs/remotes/origin/remote-only', 'HEAD')
    git('update-ref', 'refs/remotes/upstream/team/topic', 'HEAD')
    git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/remote-only')
    git('tag', 'origin/remote-only')
    git('pack-refs', '--all')
    packed_refs = (repo / '.git/packed-refs').read_bytes()
    pid = project(client, repository_path=str(repo))['id']
    url = f'/api/projects/{pid}/repository'
    index = (repo / '.git/index').read_bytes() if (repo / '.git/index').exists() else None
    result = client.get(url).json()
    assert result['branches'] == ['alpha', 'main', 'zeta']
    assert result['branch'] == 'main' and result['branches_limited'] is False
    assert result['remote_branches'] == ['origin/remote-only', 'upstream/team/topic']
    assert result['remote_branches_limited'] is False
    assert (repo / '.git/packed-refs').read_bytes() == packed_refs
    if index is not None:
        assert (repo / '.git/index').read_bytes() == index
    worktree = tmp_path / 'linked-worktree'
    git('worktree', 'add', str(worktree), 'zeta')
    linked = project(client, 'Worktree', repository_path=str(worktree))['id']
    result = client.get(f'/api/projects/{linked}/repository').json()
    assert result['branch'] == 'zeta' and result['branches'] == ['alpha', 'main', 'zeta']
    assert result['remote_branches'] == ['origin/remote-only', 'upstream/team/topic']
    git('checkout', '--detach')
    result = client.get(url).json()
    assert result['branch'] is None and result['branches'] == ['alpha', 'main', 'zeta']


def test_repository_snapshot_limits_branch_list_but_keeps_current(client, tmp_path):
    repo = tmp_path / 'many-branches'
    repo.mkdir()

    def git(*args, input=None):
        return subprocess.run(['git', '-C', str(repo), *args], input=input, check=True,
                              capture_output=True, text=True).stdout.strip()

    git('init', '-b', 'zz-current')
    git('config', 'user.name', 'Example')
    git('config', 'user.email', 'example@example.test')
    git('commit', '--allow-empty', '-m', 'Initial')
    head = git('rev-parse', 'HEAD')
    git('update-ref', '--stdin', input=''.join(
        f'create refs/heads/branch-{index:03d} {head}\n' for index in range(305)))
    git('update-ref', '--stdin', input=''.join(
        f'create refs/remotes/origin/branch-{index:03d} {head}\n' for index in range(305)))
    git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/branch-000')
    pid = project(client, repository_path=str(repo))['id']
    result = client.get(f'/api/projects/{pid}/repository').json()
    assert len(result['branches']) == 300 and result['branches_limited'] is True
    assert result['branches'] == sorted(result['branches'])
    assert 'zz-current' in result['branches']
    assert result['remote_branches'] == [f'origin/branch-{index:03d}' for index in range(300)]
    assert result['remote_branches_limited'] is True

    for index in range(6):
        git('update-ref', '-d', f'refs/heads/branch-{index:03d}')
    git('checkout', '--orphan', 'zzz-unborn')
    result = client.get(f'/api/projects/{pid}/repository').json()
    assert result['branch'] == 'zzz-unborn'
    assert len(result['branches']) == 300 and result['branches_limited'] is True
    assert 'zzz-unborn' in result['branches']
    assert result['commits'] == []
