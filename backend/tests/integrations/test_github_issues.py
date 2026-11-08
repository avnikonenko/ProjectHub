from io import BytesIO
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.app.integrations import github_issues


@pytest.mark.parametrize(('value', 'expected'), [
    ('https://github.com/Example/Project.git', 'Example/Project'),
    ('git@github.com:Example/Project', 'Example/Project'),
    ('ssh://git@github.com/Example/Project.git', 'Example/Project'),
    ('https://token@github.com/Example/Project', None),
    ('https://github.com.evil.example/Example/Project', None),
    ('https://github.com/Example/..', None),
    ('https://github.com/Example/Project/issues', None),
    ('https://github.com/Example/Project?q=secret', None),
])
def test_parse_github_remote(value, expected):
    assert github_issues.github_repository(value) == expected


def test_only_open_issues_and_canonical_links(monkeypatch):
    payload = {'items': [
        {'number': 9, 'title': 'Open', 'state': 'open', 'html_url': 'https://evil.example'},
        {'number': 8, 'title': 'PR', 'state': 'open', 'pull_request': {}},
        {'number': 7, 'title': 'Closed', 'state': 'closed'},
        *({'number': n, 'title': str(n), 'state': 'open'} for n in (6, 5, 4)),
    ]}

    class Opener:
        def open(self, request, *, timeout):
            parsed = urlsplit(request.full_url)
            assert (parsed.scheme, parsed.netloc, parsed.path) == ('https', 'api.github.com', '/search/issues')
            assert parse_qs(parsed.query) == {'q': ['repo:owner/repo is:issue is:open'],
                                              'sort': ['created'], 'order': ['desc'], 'per_page': ['3']}
            assert request.get_header('Authorization') is None
            assert timeout == 5
            return BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(github_issues, 'build_opener', lambda *_: Opener())
    result = github_issues.open_issues('owner/repo')
    assert result['url'] == 'https://github.com/owner/repo/issues'
    assert result['issues'] == [
        {'number': n, 'title': title, 'url': f'https://github.com/owner/repo/issues/{n}'}
        for n, title in ((9, 'Open'), (6, '6'), (5, '5'))
    ]


def test_upstream_error_does_not_expose_body_or_url(monkeypatch):
    class Opener:
        def open(self, request, *, timeout):
            raise HTTPError(request.full_url, 429, 'upstream details', {}, BytesIO(b'private text'))

    monkeypatch.setattr(github_issues, 'build_opener', lambda *_: Opener())
    with pytest.raises(github_issues.IssuesReadError, match=r'^GitHub limited requests\. Try again later\.$'):
        github_issues.open_issues('owner/repo')
