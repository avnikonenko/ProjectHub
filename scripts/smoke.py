"""Real HTTP/restart smoke test; uses disposable data, never an existing repository.

Run after setup: .venv/bin/python scripts/smoke.py
This tests a real local server, unlike the socketless TestClient API suite.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def run():
    with tempfile.TemporaryDirectory(prefix='projecthub-smoke-') as temporary:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'PROJECTHUB_DATA_DIR': temporary}

        def request(path, payload=None, method=None):
            body = json.dumps(payload).encode() if payload is not None else None
            req = Request(base + path, data=body, method=method,
                          headers={'Content-Type': 'application/json'})
            with urlopen(req, timeout=5) as response:
                return json.load(response)

        def start(log):
            process = subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', 'backend.app.main:create_app',
                 '--factory', '--host', '127.0.0.1', '--port', str(port)],
                cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError('Server exited before becoming ready')
                    try:
                        request('/api/health')
                        return process
                    except (URLError, TimeoutError):
                        time.sleep(0.1)
                raise RuntimeError('Server did not become ready within 10 seconds')
            except BaseException:
                stop(process)
                raise

        def stop(process):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

        with tempfile.TemporaryFile(mode='w+') as log:
            process = None
            try:
                process = start(log)
                project = request('/api/projects', {'name': 'Smoke research', 'goals': 'Verify provenance'})
                other = request('/api/projects', {'name': 'Isolation check'})
                prefix = f"/api/projects/{project['id']}"
                question = request(prefix + '/questions', {'title': 'Which method?', 'category': 'scientific'})
                qpath = prefix + f"/questions/{question['id']}"
                first = request(qpath + '/answers', {'solution': 'Method A', 'evidence': 'Controlled validation'})
                request(qpath + '/answers', {'solution': 'Method B'})
                accepted = request(qpath + '/accept', {'answer_id': first['id'], 'create_task': True})
                assert accepted['decision']['status'] == 'accepted'
                assert accepted['task']['decision_id'] == accepted['decision']['id']
                assert len(request(prefix + '/overview')['questions'][0]['answers']) == 2
                assert request(f"/api/projects/{other['id']}/overview")['questions'] == []
                assert 'Method A' in request(prefix + '/summary')['markdown']
                assert request(prefix + '/audit')
                assert request(prefix + '/search?q=Method')
                stop(process)
                process = None
                process = start(log)
                assert request(prefix + '/overview')['questions'][0]['accepted_answer_id'] == first['id']
                request(qpath + '/reopen', {})
                restored = request(prefix + '/overview')
                assert restored['questions'][0]['status'] == 'reopened'
                assert restored['decisions'][0]['status'] == 'superseded'
                with urlopen(base, timeout=5) as response:
                    assert b'<div id="root"' in response.read(), 'Build frontend before running smoke'
                print('PASS: real HTTP, question/decision/task workflow, isolation, search, restart persistence, frontend serving')
            except BaseException:
                log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                if process is not None:
                    stop(process)


if __name__ == '__main__':
    run()
