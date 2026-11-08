"""Small unauthenticated preview of a public GitHub issue list."""

import json
import re
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

_REMOTE = re.compile(
    r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)'
    r'([a-z0-9-]{1,39})/([a-z0-9_.-]{1,100}?)(?:\.git)?/?',
    re.IGNORECASE | re.ASCII,
)
_MAX_BYTES = 2 * 1024 * 1024


def github_repository(remote: str) -> str | None:
    match = _REMOTE.fullmatch(remote.strip())
    if not match or match[2] in ('.', '..'):
        return None
    return f'{match[1]}/{match[2]}'


def issues_url(repository: str) -> str:
    return f'https://github.com/{repository}/issues'


class IssuesReadError(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_issues(repository: str) -> dict:
    query = urlencode({'q': f'repo:{repository} is:issue is:open', 'sort': 'created',
                       'order': 'desc', 'per_page': 3})
    request = Request(f'https://api.github.com/search/issues?{query}', headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'ProjectHub',
    })
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=5) as response:
            raw = response.read(_MAX_BYTES + 1)
    except HTTPError as exc:
        code = exc.code
        exc.close()
        if code in (403, 429):
            raise IssuesReadError('GitHub limited requests. Try again later.') from exc
        if code in (404, 410, 422):
            raise IssuesReadError('GitHub issues are unavailable for this repository. Only public repositories are supported.') from exc
        raise IssuesReadError('Could not load issues from GitHub.') from exc
    except (URLError, OSError, HTTPException) as exc:
        raise IssuesReadError('Could not load issues from GitHub.') from exc

    try:
        data = json.loads(raw) if len(raw) <= _MAX_BYTES else None
    except (ValueError, RecursionError) as exc:
        raise IssuesReadError('GitHub returned an invalid issue list.') from exc
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise IssuesReadError('GitHub returned an invalid issue list.')
    if data.get('incomplete_results'):
        raise IssuesReadError('GitHub could not finish searching for issues. Try again later.')

    url = issues_url(repository)
    issues = []
    for item in data['items']:
        if not isinstance(item, dict):
            continue
        if 'pull_request' in item or item.get('state') != 'open':
            continue
        number, title = item.get('number'), item.get('title')
        if type(number) is int and number > 0 and isinstance(title, str):
            issues.append({'number': number, 'title': title, 'url': f'{url}/{number}'})
            if len(issues) == 3:
                break
    return {'url': url, 'issues': issues}
