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
    assert client.delete(f"/api/projects/{p['id']}").status_code in (404,405);assert repo.exists()


def test_persistence_summary_search_export_backup(tmp_path):
    data_dir=tmp_path/'data';app=create_app(data_dir)
    with TestClient(app) as c:
        p=project(c,goals='Reproducible ligand preparation');q=question(c,p['id'],'Validate protonation')
        c.post(f"/api/projects/{p['id']}/notes",json={'title':'Evidence','content':'Protonation benchmark','kind':'evidence'})
        assert c.get(f"/api/projects/{p['id']}/search",params={'q':'protonation'}).json()
        assert c.get(f"/api/projects/{p['id']}/search",params={'q':'\" OR * (x)'}).status_code==200
        summary=c.get(f"/api/projects/{p['id']}/summary").json();assert 'Reproducible ligand' in summary['markdown'];assert 'Validate protonation' in summary['markdown']
        review=c.get(f"/api/projects/{p['id']}/review").json();assert review['source']=='deterministic';assert review['findings']
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
