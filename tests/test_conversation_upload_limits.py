"""The frontend file limit and the endpoint-specific request envelope agree."""
import asyncio
import json

from starlette.requests import Request
from starlette.responses import JSONResponse

from backend.app.main import create_app


def test_only_conversation_upload_and_note_writes_accept_a_large_text_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    app = create_app(tmp_path)
    guard = next(middleware.kwargs['dispatch'] for middleware in app.user_middleware
                 if getattr(middleware.kwargs.get('dispatch'), '__name__', None) == 'local_only')
    body = json.dumps({'filename': 'large.md',
                       'content': '## User\n' + 'x' * (4 * 1024 * 1024) + '\n\n## Assistant\nThanks.'}).encode()

    async def request(method, path):
        chunks = iter(((body[:2 * 1024 * 1024], True), (body[2 * 1024 * 1024:], False)))

        async def receive():
            chunk, more = next(chunks)
            return {'type': 'http.request', 'body': chunk, 'more_body': more}

        async def target(incoming):
            assert await incoming.body() == body
            return JSONResponse({'accepted': True})

        return await guard(Request({'type': 'http', 'method': method, 'path': path, 'scheme': 'http',
                                    'server': ('testserver', 80), 'headers': []}, receive), target)

    try:
        for method, path, expected in (
            ('POST', '/api/projects/1/conversations/preview', 200),
            ('POST', '/api/projects/1/conversations/import', 200),
            ('POST', '/api/projects/1/notes', 200),
            ('PATCH', '/api/projects/1/notes/2', 200),
            ('PATCH', '/api/projects/1/conversations/import', 413),
            ('POST', '/api/projects/1/conversations/preview/extra', 413),
            ('POST', '/api/projects/1/notes/chatgpt-share', 413),
            ('POST', '/api/projects/1/tasks', 413),
        ):
            response = asyncio.run(request(method, path))
            assert response.status_code == expected, (method, path, response.body)
    finally:
        app.state.engine.dispose()
