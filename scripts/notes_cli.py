"""Local terminal bridge for AI-authored ProjectHub notes."""
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
    try:
        port = int(os.environ.get('PROJECTHUB_PORT', '8766'))
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
            raise ValueError('ProjectHub returned a server error. Check Notes before retrying; the note may have been saved.') from exc
        raise ValueError(f'ProjectHub rejected the request (HTTP {exc.code}).') from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise ValueError('Could not confirm the ProjectHub response. Start ProjectHub if needed. '
                         'For a note submission, check Notes before retrying to avoid a duplicate.') from exc


def resolve_project(projects, args):
    matches = [p for p in projects if p['id'] == args.project_id] if args.project_id is not None else [
        p for p in projects if p['name'] == args.project]
    if len(matches) != 1:
        raise ValueError('Project not found or name is ambiguous. Run projecthub projects and use --project-id.')
    return matches[0]


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest='command', required=True)
    commands.add_parser('projects', help='List project IDs and names')
    notes = commands.add_parser('note', help='Add an AI-authored note')
    actions = notes.add_subparsers(dest='action', required=True)
    add = actions.add_parser('add')
    project = add.add_mutually_exclusive_group(required=True)
    project.add_argument('--project', help='Exact project name')
    project.add_argument('--project-id', type=int)
    add.add_argument('--title', required=True)
    content = add.add_mutually_exclusive_group(required=True)
    content.add_argument('--content')
    content.add_argument('--file', type=Path, help='UTF-8 Markdown/text file')
    content.add_argument('--stdin', action='store_true', help='Read Markdown/text from standard input')
    add.add_argument('--session', help='Originating session ID (defaults to CODEX_THREAD_ID)')
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'projects':
            projects = request('GET', '/projects')
            print(json.dumps([{'id': p['id'], 'name': p['name']} for p in projects], ensure_ascii=False))
            return 0
        title = args.title.strip()
        if not title or len(title) > 500:
            raise ValueError('Title must contain 1–500 characters.')
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
