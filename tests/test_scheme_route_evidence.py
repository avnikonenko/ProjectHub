import ast
from pathlib import Path

from backend.app.integrations.codex.scheme_route_evidence import python_route_declarations


def _extract(source):
    return python_route_declarations(ast.parse(source))


def test_extracts_lexical_http_paths_and_hash_navigation():
    rows = _extract('''
def create_app():
    @app.get('/api/projects/{project_id}/history/search')
    async def search_conversations():
        return {'href': '#history?' + urlencode(params)}

    @project_router.api_route(path='/items/{item_id:int}/', methods=['GET', 'PATCH', 'PATCH'])
    def change_item():
        return {'href': f'#knowledge?{encoded_values}'}
''')
    assert rows == [
        (3, 'HTTP route GET /api/projects/{project_id}/history/search -> search_conversations'),
        (5, 'search_conversations builds navigation link #history?'),
        (7, 'HTTP route GET /items/{item_id:int}/ -> change_item'),
        (7, 'HTTP route PATCH /items/{item_id:int}/ -> change_item'),
        (9, 'change_item builds navigation link #knowledge?'),
    ]


def test_keeps_path_structure_and_omits_unrelated_literals_and_values():
    rows = _extract('''
@app.get('/known', description='secret-description')
@app.get('/query?token=secret-query')
@app.get('https://example.test/secret-external')
@app.get('//example.test/secret-relative')
@app.get(f'/dynamic/{unknown}')
@mapping.get('/unrelated')
def handler():
    return [
        {'text': '#unrelated?'},
        {'href': 'https://example.test/secret-href'},
        {'href': '#history?token=secret-fragment'},
        {'href': '#history?' + urlencode({'token': 'secret-value'})},
    ]
''')
    assert [declaration for _, declaration in rows] == [
        'HTTP route GET /known -> handler',
        'handler builds navigation link #history?',
    ]


def test_projecthub_search_declarations_come_from_current_code():
    repository = Path(__file__).resolve().parents[1]
    application = _extract((repository / 'backend/app/main.py').read_text(encoding='utf-8'))
    results = _extract((repository / 'backend/app/project_search.py').read_text(encoding='utf-8'))
    assert any(row == 'HTTP route GET /api/projects/{project_id}/history/search -> search_conversations'
               for _, row in application)
    assert any(row == 'HTTP route GET /api/projects/{project_id}/search/all -> search_all'
               for _, row in application)
    assert any(row == 'project_search builds navigation link #history?' for _, row in results)
