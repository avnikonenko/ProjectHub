"""Focused Scheme storage, citations and progress with a mocked provider."""
from importlib import import_module
import json
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import ValidationError
import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import focused_scheme, progress
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import Project, ProjectScheme

routes = import_module('backend.app.integrations.codex.router')


@pytest.fixture
def database(tmp_path):
    engine, _ = initialize(tmp_path)
    try:
        with Session(engine) as db:
            db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')])
            db.flush()
            db.add(ProjectScheme(project_id=1, snapshot={'generated_at': 'earlier', 'architecture': {'nodes': []}}))
            db.commit()
        yield engine
    finally:
        engine.dispose()


def diagram(source='repo:search'):
    return {
        'nodes': [{'id': 'index', 'label': 'Index saved text', 'description': 'Store searchable text.',
                   'type': 'operation', 'status': 'observed', 'source_ids': [source]}],
        'edges': [],
        'explanation': 'The index receives the saved text.',
    }


def test_focused_route_saves_instruction_and_cited_graph_separately(database, monkeypatch):
    calls = []
    monkeypatch.setenv('PROJECTHUB_SCHEME_MAX_NODES', '1')

    def evidence(engine, pid, limits, *, scope):
        assert engine is database and pid == 1
        calls.append(scope)
        return {'sources': [{'id': 'repo:search', 'label': 'search.py',
                             'text': 'Index each saved turn.', 'path': 'search.py'}],
                'coverage': {'partial': False, 'limitations': []}}

    class Manager:
        response = diagram()

        def generate_brief(self, prompt, schema):
            nodes = schema['properties']['nodes']
            assert nodes['maxItems'] == 1
            assert nodes['items']['properties']['source_ids']['items']['enum'] == ['repo:search']
            assert 'Limit the node count to 1 and the edge count to 40.' in prompt
            assert json.loads(prompt.splitlines()[-2])['user_instruction'] == calls[-1]
            return json.dumps(self.response)

    manager = Manager()
    monkeypatch.setattr(focused_scheme, 'collect_evidence', evidence)
    monkeypatch.setattr(routes, 'generation_base', lambda *_: manager)
    monkeypatch.setattr(routes, 'review_manager', lambda selected, *_: selected)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=database)))

    with Session(database) as db:
        assert routes.get_focused_scheme(1, db) == {'snapshot': None}
        assert routes.get_focused_scheme(2, db) == {'snapshot': None}
        body = routes.FocusedSchemeGenerationInput(instruction='  How does indexing work?  ')
        with pytest.raises(HTTPException) as unconfirmed:
            routes.generate_focused_scheme(1, body, request, db)
        assert unconfirmed.value.status_code == 422 and calls == []
        body.confirmed = True
        saved = routes.generate_focused_scheme(1, body, request, db)
        assert saved['instruction'] == 'How does indexing work?'
        assert saved['graph']['nodes'][0]['paths'] == ['search.py']
        assert 'Index saved text' in saved['graph']['mermaid']
        assert routes.get_focused_scheme(1, db) == {'snapshot': saved}
        attempt = routes.generation_status(1, db, kind='focused_scheme')['latest_attempt']
        assert attempt['state'] == 'completed'
        assert attempt['settings']['scheme_max_nodes'] == 1
        assert routes.get_focused_scheme(2, db) == {'snapshot': None}
        with pytest.raises(HTTPException) as missing:
            routes.get_focused_scheme(999, db)
        assert missing.value.status_code == 404

        manager.response = diagram(source='foreign')
        with pytest.raises(HTTPException, match='foreign evidence') as rejected:
            routes.generate_focused_scheme(1, body, request, db)
        assert rejected.value.status_code == 503
        manager.response = diagram()
        manager.response['nodes'].append({**manager.response['nodes'][0], 'id': 'query'})
        with pytest.raises(HTTPException, match='graph size limit.*maximum nodes 1') as oversized:
            routes.generate_focused_scheme(1, body, request, db)
        assert oversized.value.status_code == 503
        assert routes.get_focused_scheme(1, db) == {'snapshot': saved}
        assert db.get(ProjectScheme, 1).snapshot['generated_at'] == 'earlier'


def test_cancelled_focused_scheme_keeps_saved_result(database, monkeypatch):
    monkeypatch.setattr(focused_scheme, 'collect_evidence', lambda *args, **kwargs: {
        'sources': [{'id': 'repo:search', 'text': 'Index each turn.'}],
        'coverage': {'partial': False, 'limitations': []},
    })

    class Manager:
        cancelled = False

        def generate_brief(self, prompt, schema):
            if self.cancelled:
                status = progress.read(1, 'focused_scheme')
                progress.cancel(1, 'focused_scheme', status['started_at'])
            return json.dumps(diagram())

    manager = Manager()
    saved = focused_scheme.refresh_focused_scheme(database, 1, manager, 'Show indexing.')
    manager.cancelled = True
    with pytest.raises(CodexError, match='cancelled'):
        focused_scheme.refresh_focused_scheme(database, 1, manager, 'Show storage.')
    with Session(database) as db:
        assert focused_scheme.read_focused_scheme(db, 1) == saved
        assert routes.generation_status(1, db, kind='focused_scheme')['latest_attempt']['state'] == 'cancelled'
        assert all(view['kind'] != 'focused_scheme' for view in routes.generation_summary(1, db)['views'])


@pytest.mark.parametrize('instruction', ['', '   ', 'x' * 2001, 'bad\u0000instruction'])
def test_invalid_focused_instruction(instruction):
    with pytest.raises(ValidationError):
        routes.FocusedSchemeGenerationInput(confirmed=True, instruction=instruction)


@pytest.mark.parametrize('rewrite', ['supported', 'invalid', 'unrelated'])
def test_focused_named_evidence_gets_one_optional_coverage_retry(database, monkeypatch, rewrite):
    monkeypatch.setattr(focused_scheme, 'collect_evidence', lambda *args, **kwargs: {
        'sources': [
            {'id': 'repo:template', 'path': 'templates/methods.in', 'provenance': 'repository configuration',
             'text': 'L1: Active namelist section &first\nL4: Commented-out namelist section &second'},
            {'id': 'repo:search', 'text': 'Index each turn.'},
        ],
        'coverage': {'partial': False, 'limitations': []},
    })
    prompts = []

    class Manager:
        def generate_brief(self, prompt, schema):
            prompts.append(prompt)
            if len(prompts) == 1:
                return json.dumps({'nodes': [], 'edges': [], 'explanation': 'The template is available.'})
            return json.dumps(diagram({'supported': 'repo:template', 'invalid': 'foreign',
                                       'unrelated': 'repo:search'}[rewrite]))

    saved = focused_scheme.refresh_focused_scheme(database, 1, Manager(), 'Explain methods.in defaults')
    assert len(prompts) == saved['diagnostics']['ai_requests'] == 2
    assert 'repo:template' in prompts[1].splitlines()[0]
    assert bool(saved['graph']['nodes']) == (rewrite == 'supported')
    assert saved['coverage']['partial'] == (rewrite != 'supported')


@pytest.mark.parametrize('repair', ['valid', 'reversed', 'empty', 'expired'])
def test_focused_checks_actual_handoff_edge_and_rejects_reversed_direction(database, monkeypatch, repair):
    clock = [1000]
    if repair == 'expired':
        monkeypatch.setenv('PROJECTHUB_GENERATION_BUDGET', '60')
        monkeypatch.setattr(progress.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(focused_scheme, 'collect_evidence', lambda *args, **kwargs: {
        'sources': [{'id': 'repo:file', 'path': 'example.py', 'text': 'Bounded code',
                     'local_handoffs': [{'type': 'value_transfer',
                                         'from_symbol': 'read', 'from_line': 2,
                                         'to_symbol': 'write', 'to_line': 3,
                                         'context': 'write consumes the result of read'}]}],
        'coverage': {'partial': False, 'limitations': []},
    })

    class Manager:
        calls = 0

        def generate_brief(self, prompt, schema):
            self.calls += 1
            if self.calls == 2:
                if repair == 'empty':
                    return json.dumps({'nodes': [], 'edges': [], 'explanation': 'No relationships available.'})
                if repair == 'expired':
                    clock[0] += 31
                    progress.check_cancelled()
            contract, = json.loads(prompt.splitlines()[-1])['required_handoffs']
            graph = {'nodes': [
                {**diagram('repo:file')['nodes'][0], 'id': contract[key]['id'], 'label': contract[key]['label']}
                for key in ('source_node', 'target_node')
            ], 'edges': [], 'explanation': 'The source declares two operations.'}
            order = ('source_node', 'target_node') if self.calls == 2 and repair == 'valid' else ('target_node', 'source_node')
            graph['edges'].append({'source': contract[order[0]]['id'], 'target': contract[order[1]]['id'],
                                   'type': 'value_transfer', 'status': 'observed',
                                   'label': 'Model wording', 'source_ids': ['repo:file']})
            return json.dumps(graph)

    manager = Manager()
    saved = focused_scheme.refresh_focused_scheme(database, 1, manager, 'Trace read into write')
    assert manager.calls == 2
    assert bool(saved['graph']['edges']) == (repair == 'valid')
    assert saved['coverage']['partial'] == (repair != 'valid')
    if repair == 'valid':
        assert saved['graph']['edges'][0]['label'] == 'Direct value transfer'
        assert {node['description'] for node in saved['graph']['nodes']} == {'write consumes the result of read'}
    else:
        assert len(saved['graph']['nodes']) == 2


def test_focused_reserved_edge_type_is_normalized_before_handoff_checks():
    raw = diagram('repo:file')
    raw['nodes'].append({**raw['nodes'][0], 'id': 'store'})
    raw['edges'].append({'source': raw['nodes'][0]['id'], 'target': 'store', 'type': 'value_transfer ',
                         'status': 'observed', 'label': 'Unsupported', 'source_ids': ['repo:file']})
    graph = focused_scheme._graph(json.dumps(raw), {'repo:file': {'id': 'repo:file'}})
    assert focused_scheme.check_contracts(graph, []) == (
        set(), {(raw['nodes'][0]['id'], 'store', 'value_transfer')})
