"""Real Firefox Q&A revision/recovery regression, using an isolated temporary DB.

Run from the repository root after building frontend:
    .venv/bin/python scripts/check-qa-view.py
Requires Firefox with Marionette; makes no AI calls or production-data writes.
"""
import os,sys,time,json,socket,subprocess,tempfile,urllib.request,base64,shutil
from pathlib import Path
os.umask(0o077);root=Path(tempfile.mkdtemp(prefix='ph-qa-ui-review-'));profile_root=Path.home()/'snap/firefox/common';profile=Path(tempfile.mkdtemp(prefix='projecthub-evaluation-',dir=profile_root if profile_root.is_dir() else None))
def freeport():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
httpport,marionette=freeport(),freeport();url=f'http://127.0.0.1:{httpport}'
(profile/'user.js').write_text(f'user_pref("marionette.port", {marionette});\nuser_pref("browser.shell.checkDefaultBrowser", false);\nuser_pref("browser.startup.page", 0);\n')
env=dict(os.environ);env['PROJECTHUB_DATA_DIR']=str(root/'http');env['PROJECTHUB_HISTORY_AUTO_SYNC']='false'
log=open(root/'browser-server.log','ab');browserlog=open(root/'firefox.log','ab')
server=browser=None;connection=None
try:
 server=subprocess.Popen([str(Path('.venv/bin/python').absolute()),'-m','uvicorn','backend.app.main:create_app','--factory','--host','127.0.0.1','--port',str(httpport)],env=env,stdout=log,stderr=subprocess.STDOUT)
 for _ in range(100):
  try:
   with urllib.request.urlopen(url+'/api/health',timeout=1) as r:assert r.status==200
   break
  except OSError:time.sleep(.1)
 else:raise RuntimeError('Isolated HTTP server failed')
 def request(path, body=None):
  req=urllib.request.Request(url+'/api'+path, data=json.dumps(body).encode() if body is not None else None, headers={'Content-Type':'application/json'})
  with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)
 project=request('/projects',{'name':'Q&A isolated review'})
 pid=project['id']
 old={'generated_at':'2026-11-15T10:00:00Z','items':[{'question':'Historical question?','answer':'HISTORICAL_ANSWER_MARKER','source_ids':[]}],'sources':[],'generation':None}
 latest={'generated_at':'2026-11-16T10:00:00Z','items':[{'question':'Latest question?','answer':'LATEST_ANSWER_MARKER','source_ids':[]}],'sources':[],'generation':None}
 import sqlite3
 with sqlite3.connect(root/'http/projecthub.sqlite3') as db:
  db.execute('INSERT INTO project_qa(project_id,generated_at,items,sources,generation) VALUES(?,?,?,?,?)',(pid,latest['generated_at'],json.dumps(latest['items']),'[]',None))
  for value in (old,latest):db.execute('INSERT INTO generation_revisions(project_id,kind,generated_at,snapshot) VALUES(?,?,?,?)',(pid,'qa',value['generated_at'],json.dumps(value)))
 browser=subprocess.Popen(['/usr/bin/firefox','--headless','--marionette','--no-remote','--profile',str(profile),'about:blank'],stdout=browserlog,stderr=subprocess.STDOUT)
 for _ in range(100):
  try:connection=socket.create_connection(('127.0.0.1',marionette),timeout=1);connection.settimeout(15);break
  except OSError:time.sleep(.2)
 else:raise RuntimeError('Firefox Marionette unavailable')
 def recv():
  length=b''
  while True:
   ch=connection.recv(1)
   if not ch:raise RuntimeError('Browser disconnected')
   if ch==b':':break
   length+=ch
  remaining=int(length);chunks=[]
  while remaining:
   part=connection.recv(remaining)
   if not part:raise RuntimeError('Browser disconnected')
   chunks.append(part);remaining-=len(part)
  return json.loads(b''.join(chunks))
 recv();seq=0
 def command(name,params):
  global seq
  seq+=1;payload=json.dumps([0,seq,name,params]).encode();connection.sendall(str(len(payload)).encode()+b':'+payload)
  response=recv()
  if response[2] is not None:raise RuntimeError(str(response[2]))
  return response[3]
 def js(script):
  result=command('WebDriver:ExecuteScript',{'script':script,'args':[],'newSandbox':True})
  return result.get('value') if isinstance(result,dict) and 'value' in result else result
 command('WebDriver:NewSession',{'capabilities':{'alwaysMatch':{'acceptInsecureCerts':False}}})
 command('WebDriver:Navigate',{'url':url+f'/#knowledge?project={pid}'})
 def wait(script):
  for _ in range(100):
   value=js(script)
   if value:return value
   time.sleep(.1)
  raise AssertionError(script)
 wait("return !![...document.querySelectorAll('.tabs button')].find(e=>e.textContent.trim()==='AI Q&A')")
 js("window.__qaReads=0;const original=window.fetch.bind(window);window.fetch=async(input,options)=>{const u=String(input);if(u.includes('/generation/status?kind=tags')) return Response.json({state:'completed',started_at:'2026-11-16T09:00:00Z',stage:'Completed',sessions:0,turns:0,batches:0,error:null});const response=await original(input,options);if(u.endsWith('/codex/qa')&&(!options?.method||options.method==='GET')){window.__qaReads++;if(window.__qaReads===2)await new Promise(resolve=>window.__qaRelease=resolve);}return response;};return true;")
 js("[...document.querySelectorAll('.tabs button')].find(e=>e.textContent.trim()==='AI Q&A').click();return true;")
 wait("return document.body.textContent.includes('LATEST_ANSWER_MARKER')")
 wait("return typeof window.__qaRelease==='function'")
 js("[...document.querySelectorAll('summary')].find(e=>e.textContent==='Q&A details & options').parentElement.open=true;document.querySelector('.revision-history details').open=true;return true;")
 wait("return document.querySelectorAll('.revision-history ul li button').length===2")
 js("document.querySelectorAll('.revision-history ul li button')[1].click();return true;")
 wait("return document.body.textContent.includes('HISTORICAL_ANSWER_MARKER')")
 before=js("return {historical:document.body.textContent.includes('HISTORICAL_ANSWER_MARKER'),previous:document.body.textContent.includes('Viewing saved version'),reads:window.__qaReads}")
 js("window.__qaRelease();return true;")
 time.sleep(.7)
 after=js("return {historical:document.body.textContent.includes('HISTORICAL_ANSWER_MARKER'),latest:document.body.textContent.includes('LATEST_ANSWER_MARKER'),previous:document.body.textContent.includes('Viewing saved version'),reads:window.__qaReads}")
 assert after['historical'] and after['previous'] and not after['latest'], after
 js("[...document.querySelectorAll('.revision-history button')].find(e=>e.textContent.trim()==='Return to latest').click();return true;")
 wait("return document.body.textContent.includes('LATEST_ANSWER_MARKER') && !document.body.textContent.includes('Viewing saved version')")
 assert js("return !document.body.textContent.includes('Loading saved Q&A version')")
 result={'return_to_latest':True,'real_firefox':True,'before_delayed_reload':before,'after_delayed_reload':after,'historical_selection_overwritten':before['historical'] and not after['historical'],'real_ai_calls':0}
 (root/'qa-review-results.json').write_text(json.dumps(result,indent=2));print(json.dumps({'artifacts':str(root),**result}),flush=True)
 command('WebDriver:DeleteSession',{})

finally:
 if connection:connection.close()
 for proc in (browser,server):
  if proc is not None:
   proc.terminate()
   try:proc.wait(timeout=5)
   except subprocess.TimeoutExpired:proc.kill();proc.wait()
 log.close();browserlog.close()
 shutil.rmtree(profile)
