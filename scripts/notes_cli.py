"""Local terminal bridge for ProjectHub notes, TODOs, and linked sessions."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_CONTENT = 200_000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(method, path, payload=None):
    from backend.app.config import load_settings
    try:
        port = load_settings().port
        if not 1 <= port <= 65535:
            raise ValueError()
    except ValueError:
        raise ValueError('PROJECTHUB_PORT must be a valid local port.')
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    req = Request(f'http://127.0.0.1:{port}/api{path}', data=data, method=method,
                  headers={'Content-Type': 'application/json'})
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        if method == 'POST' and exc.code >= 500:
            raise ValueError('ProjectHub returned a server error. Check Notes, TODO, or Sources before retrying; the item may have been saved.') from exc
        raise ValueError(f'ProjectHub rejected the request (HTTP {exc.code}).') from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise ValueError('Could not confirm the ProjectHub response. Start ProjectHub if needed. '
                         'For a submission, check Notes, TODO, or Sources before retrying to avoid a duplicate.') from exc


def resolve_project(projects, args):
    matches = [p for p in projects if p['id'] == args.project_id] if args.project_id is not None else [
        p for p in projects if p['name'] == args.project]
    if len(matches) != 1:
        raise ValueError('Project not found or name is ambiguous. Run projecthub projects and use --project-id.')
    return matches[0]


def project_arguments(command):
    project = command.add_mutually_exclusive_group(required=True)
    project.add_argument('--project', help='Exact project name')
    project.add_argument('--project-id', type=int)


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest='command', required=True)
    commands.add_parser('projects', help='List project IDs and names')
    notes = commands.add_parser('note', aliases=['notes'], help='Read notes or add an AI-authored note')
    actions = notes.add_subparsers(dest='action', required=True)
    add = actions.add_parser('add')
    project_arguments(add)
    project_arguments(actions.add_parser('list'))
    add.add_argument('--title', required=True)
    content = add.add_mutually_exclusive_group(required=True)
    content.add_argument('--content')
    content.add_argument('--file', type=Path, help='UTF-8 Markdown/text file')
    content.add_argument('--stdin', action='store_true', help='Read Markdown/text from standard input')
    add.add_argument('--session', help='Originating session ID (defaults to CODEX_THREAD_ID)')
    todos = commands.add_parser('todo', help='Read or add checklist items')
    todo_actions = todos.add_subparsers(dest='action', required=True)
    listing = todo_actions.add_parser('list')
    project_arguments(listing)
    listing.add_argument('--all', action='store_true', help='Include completed TODOs')
    todo_add = todo_actions.add_parser('add')
    project_arguments(todo_add)
    todo_add.add_argument('--title', required=True)
    done = todo_actions.add_parser('complete', aliases=['done'], help='Mark an existing TODO completed')
    project_arguments(done)
    done.add_argument('--todo-id', type=int, required=True)
    sessions = commands.add_parser('session', help='List or link Codex sessions for reading')
    session_actions = sessions.add_subparsers(dest='action', required=True)
    project_arguments(session_actions.add_parser('list'))
    linking = session_actions.add_parser('link')
    project_arguments(linking)
    linking.add_argument('--session', help='Codex thread ID (defaults to CODEX_THREAD_ID)')
    linking.add_argument('--title', help='Optional display name (otherwise use the Codex name)')
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'projects':
            projects = request('GET', '/projects')
            print(json.dumps([{'id': p['id'], 'name': p['name']} for p in projects], ensure_ascii=False))
            return 0
        if args.command == 'todo' and args.action in ('complete', 'done'):
            if args.todo_id <= 0:
                raise ValueError('TODO ID must be a positive integer.')
            project = resolve_project(request('GET', '/projects'), args)
            path = f"/projects/{project['id']}/tasks"
            rows = request('GET', path)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError('Unexpected TODO list response.')
            item = next((row for row in rows if row.get('id') == args.todo_id), None)
            if item is None:
                raise ValueError('TODO not found in this project. Run projecthub todo list --all.')
            already_completed = item.get('status') == 'completed'
            result = item if already_completed else request('PATCH', f"{path}/{args.todo_id}", {'status': 'completed'})
            if (not isinstance(result, dict) or result.get('id') != args.todo_id
                    or result.get('project_id') != project['id'] or result.get('status') != 'completed'):
                raise ValueError('Could not confirm completion. Check projecthub todo list --all before retrying.')
            print(json.dumps({'todo_id': args.todo_id, 'project_id': project['id'],
                              'project': project['name'], 'status': 'completed',
                              'already_completed': already_completed}, ensure_ascii=False))
            return 0
        if args.command == 'session':
            session = None
            if args.action == 'link':
                session = args.session if args.session is not None else os.environ.get('CODEX_THREAD_ID', '')
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', session):
                    raise ValueError('Provide a valid --session or run from a Codex session with CODEX_THREAD_ID.')
                if args.title is not None and not 1 <= len(args.title.strip()) <= 200:
                    raise ValueError('Session title must contain 1–200 characters.')
            project = resolve_project(request('GET', '/projects'), args)
            path = f"/projects/{project['id']}/codex/sessions"
            rows = request('GET', path)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError('Unexpected session list response.')
            if args.action == 'list':
                print(json.dumps({'project_id': project['id'], 'sessions': rows}, ensure_ascii=False))
                return 0
            existing = next((row for row in rows if row.get('thread_id') == session
                             and row.get('is_linked') is True), None)
            payload = {'thread_id': session}
            if args.title is not None:
                payload['title'] = args.title.strip()
            result = existing if existing is not None else request('POST', path, payload)
            if (not isinstance(result, dict) or type(result.get('id')) is not int
                    or result.get('project_id') != project['id'] or result.get('thread_id') != session
                    or result.get('is_linked') is not True):
                raise ValueError('Unexpected session response. Check Sources or projecthub session list before retrying; '
                                 'the session may already be linked.')
            print(json.dumps({'session_id': result['id'], 'thread_id': session, 'project_id': project['id'],
                              'project': project['name'], 'title': result.get('title', ''),
                              'already_linked': existing is not None}, ensure_ascii=False))
            return 0
        if args.action == 'list':
            project = resolve_project(request('GET', '/projects'), args)
            collection = 'tasks' if args.command == 'todo' else 'notes'
            items = request('GET', f"/projects/{project['id']}/{collection}")
            if args.command == 'todo' and not args.all:
                items = [item for item in items if item['status'] != 'completed']
            print(json.dumps({'project_id': project['id'], collection: items}, ensure_ascii=False))
            return 0
        title = args.title.strip()
        if not title or len(title) > 500:
            raise ValueError('Title must contain 1–500 characters.')
        if args.command == 'todo':
            project = resolve_project(request('GET', '/projects'), args)
            result = request('POST', f"/projects/{project['id']}/tasks", {'title': title})
            if (not isinstance(result, dict) or not isinstance(result.get('id'), int)
                    or result.get('project_id') != project['id']):
                raise ValueError('Unexpected TODO response. Check TODO before retrying; the item may have been saved.')
            print(json.dumps({'todo_id': result['id'], 'project_id': project['id'], 'project': project['name']}, ensure_ascii=False))
            return 0
        if args.file:
            with args.file.open(encoding='utf-8') as stream:
                content = stream.read(MAX_CONTENT + 1)
        elif args.stdin:
            content = sys.stdin.read(MAX_CONTENT + 1)
        else:
            content = args.content
        if not content.strip() or len(content) > MAX_CONTENT:
            raise ValueError('Note text must contain 1–200,000 characters.')
        session = args.session if args.session is not None else os.environ.get('CODEX_THREAD_ID', '')
        if session and not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', session):
            raise ValueError('Session ID must contain only letters, numbers, underscores, or hyphens.')
        project = resolve_project(request('GET', '/projects'), args)
        result = request('POST', f"/projects/{project['id']}/notes", {
            'title': title, 'content': content, 'kind': 'ai_note',
            'reference': f'AI terminal session {session}' if session else 'AI terminal session (ID not supplied)',
        })
        if (not isinstance(result, dict) or not isinstance(result.get('id'), int)
                or result.get('project_id') != project['id'] or result.get('kind') != 'ai_note'):
            raise ValueError('Unexpected note response. Check Notes before retrying; the note may have been saved.')
        print(json.dumps({'note_id': result['id'], 'project_id': project['id'],
                          'project': project['name'], 'kind': 'ai_note'}, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print(f'projecthub: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
