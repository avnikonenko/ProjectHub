"""Focused Scheme storage, citations and progress with a mocked provider."""
from importlib import import_module
from itertools import islice, permutations
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
    monkeypatch.setenv('PROJECTHUB_SCHEME_MAX_EDGES', '0')

    def evidence(engine, pid, limits, *, scope):
        assert engine is database and pid == 1
        calls.append(scope)
        return {'sources': [{'id': 'repo:search', 'label': 'search.py',
                             'text': 'Index each saved turn.', 'path': 'search.py'}],
                'coverage': {'partial': False, 'limitations': []}}

    class Manager:
        response = diagram()
        node_limit = 1

        def generate_brief(self, prompt, schema):
            nodes = schema['properties']['nodes']
            assert nodes['maxItems'] == self.node_limit
            assert schema['properties']['edges']['maxItems'] == 0
            assert nodes['items']['properties']['source_ids']['items']['enum'] == ['repo:search']
            assert f'Limit the node count to {self.node_limit} and the edge count to 0.' in prompt
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
        assert attempt['settings']['scheme_max_edges'] == 0
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
        manager.node_limit = 2
        monkeypatch.setenv('PROJECTHUB_SCHEME_MAX_NODES', str(manager.node_limit))
        manager.response['edges'].append({'source': 'index', 'target': 'query', 'label': '', 'type': 'uses',
                                          'status': 'observed', 'source_ids': ['repo:search']})
        with pytest.raises(HTTPException, match='graph size limit.*maximum nodes 2, maximum edges 0') as oversized:
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
@pytest.mark.parametrize('max_edges', [0, 40])
def test_focused_named_evidence_gets_one_optional_coverage_retry(database, monkeypatch, rewrite, max_edges):
    monkeypatch.setenv('PROJECTHUB_SCHEME_MAX_EDGES', str(max_edges))
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


@pytest.mark.parametrize('max_nodes,max_edges,contract_fits', [(2, 0, False), (1, 40, False), (2, 1, True)])
def test_focused_skips_handoff_retries_that_cannot_fit(database, monkeypatch, max_nodes, max_edges, contract_fits):
    monkeypatch.setenv('PROJECTHUB_SCHEME_MAX_NODES', str(max_nodes))
    monkeypatch.setenv('PROJECTHUB_SCHEME_MAX_EDGES', str(max_edges))
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
            assert self.calls == 1
            package = json.loads(prompt.splitlines()[-1])
            assert bool(package.get('required_handoffs')) == contract_fits
            assert package['sources'][0]['local_handoffs']
            assert schema['properties']['edges']['maxItems'] == max_edges
            result = diagram('repo:file')
            if max_nodes > 1:
                result['nodes'].append({**result['nodes'][0], 'id': 'another'})
            return json.dumps(result)

    saved = focused_scheme.refresh_focused_scheme(database, 1, Manager(), 'Trace read into write')
    assert saved['diagnostics']['ai_requests'] == 1
    assert len(saved['graph']['nodes']) == max_nodes and not saved['graph']['edges']
    assert saved['coverage']['partial']
    assert any(('could not be verified' if contract_fits else 'configured graph limits') in warning
               for warning in saved['coverage']['limitations'])


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


def _handoff_source():
    return {'id': 'repo:file', 'path': 'example.py', 'text': 'Bounded code',
            'local_handoffs': [{'type': 'value_transfer',
                                'from_symbol': 'read', 'from_line': 2,
                                'to_symbol': 'write', 'to_line': 3,
                                'context': 'write consumes the result of read'}]}


def _handoff_diagram(contract):
    nodes = [{**diagram(contract['source_id'])['nodes'][0],
              'id': contract[endpoint]['id'], 'label': contract[endpoint]['label']}
             for endpoint in ('source_node', 'target_node')]
    edge = {'source': nodes[0]['id'], 'target': nodes[1]['id'], 'type': contract['type'],
            'status': 'observed', 'label': 'Transfer', 'source_ids': [contract['source_id']]}
    return {'nodes': nodes, 'edges': [edge], 'explanation': 'The source declares two operations.'}


@pytest.mark.parametrize('rewrite', ['valid', 'reversed', 'description', 'unrelated'])
def test_focused_retry_corrects_only_verified_endpoint_metadata(database, monkeypatch, rewrite):
    source = _handoff_source()
    monkeypatch.setattr(focused_scheme, 'collect_evidence', lambda *args, **kwargs: {
        'sources': [source], 'coverage': {'partial': False, 'limitations': []},
    })
    contract, = focused_scheme.make_contracts([source])

    class Manager:
        calls = 0

        def generate_brief(self, prompt, schema):
            self.calls += 1
            graph = _handoff_diagram(contract)
            graph['nodes'].append(diagram(source['id'])['nodes'][0])
            if self.calls == 1:
                graph['nodes'][0]['label'] = 'Incorrect read label'
                graph['nodes'][1]['status'] = 'uncertain'
                if rewrite == 'reversed':
                    edge = graph['edges'][0]
                    edge['source'], edge['target'] = edge['target'], edge['source']
            elif rewrite == 'description':
                graph['nodes'][0]['description'] = 'Changed description'
            elif rewrite == 'unrelated':
                graph['nodes'][2]['label'] = 'Changed unrelated label'
            return json.dumps(graph)

    manager = Manager()
    saved = focused_scheme.refresh_focused_scheme(database, 1, manager, 'Trace read into write')
    assert manager.calls == 2
    assert saved['graph']['nodes'][2]['label'] == 'Index saved text'
    if rewrite in ('valid', 'reversed'):
        assert saved['graph']['nodes'][0]['label'] == 'read'
        assert saved['graph']['nodes'][1]['status'] == 'observed'
        assert saved['graph']['edges'][0]['label'] == 'Direct value transfer'
        assert not saved['coverage']['partial']
    else:
        assert saved['graph']['nodes'][0]['label'] == 'Incorrect read label'
        assert saved['graph']['nodes'][0]['description'] == 'Store searchable text.'
        assert not saved['graph']['edges']
        assert saved['coverage']['partial']


@pytest.mark.parametrize('patch_valid', [True, False])
def test_large_focused_retry_uses_a_bounded_patch(database, monkeypatch, patch_valid):
    monkeypatch.setenv('PROJECTHUB_SCHEME_MAX_NODES', '60')
    monkeypatch.setenv('PROJECTHUB_SCHEME_MAX_EDGES', '120')
    source = _handoff_source()
    references = [f'repo:{"nested/" * 22}module{index}.py' for index in range(6)]
    named = {'id': 'repo:template', 'path': 'methods.in', 'provenance': 'repository configuration',
             'text': 'L1: Active namelist section &first'}
    sources = [source, named, *({'id': ref, 'text': 'Recorded observations. ' * 75} for ref in references)]
    monkeypatch.setattr(focused_scheme, 'collect_evidence', lambda *args, **kwargs: {
        'sources': sources, 'coverage': {'partial': False, 'limitations': []},
    })
    contract, = focused_scheme.make_contracts([source])
    graph = _handoff_diagram(contract)
    required_edge = graph['edges'].pop()
    for index in range(58):
        graph['nodes'].append({
            **graph['nodes'][0], 'id': f'existing{index}', 'source_ids': references,
            'description': 'Recorded observations. ' * 17,
        })
    for start, end in islice(permutations(range(58), 2), 119):
        graph['edges'].append({
            **required_edge, 'source': f'existing{start}', 'target': f'existing{end}',
            'type': 'uses', 'label': 'Recorded relationship between these two components.',
            'source_ids': references,
        })
    draft = json.dumps(graph, separators=(',', ':'))

    class Manager:
        calls = 0

        def generate_brief(self, prompt, schema):
            self.calls += 1
            if self.calls == 1:
                assert len(prompt) + len(draft) > focused_scheme.MAX_INPUT_CHARS
                return draft
            assert self.calls == 2
            assert len(prompt) < focused_scheme.MAX_INPUT_CHARS
            assert set(schema['properties']) == {'nodes', 'edges'}
            package = json.loads(prompt.splitlines()[-1])
            assert package['required_handoffs'] == [contract]
            assert package['missing_source_ids'] == [named['id']]
            assert {item['id'] for item in package['sources']} == {source['id'], named['id']}
            patched_node = {**graph['nodes'][0], 'source_ids': [source['id'], named['id']]}
            edge = required_edge if patch_valid else {**required_edge, 'target': 'existing1'}
            return json.dumps({'nodes': [patched_node], 'edges': [edge]})

    manager = Manager()
    saved = focused_scheme.refresh_focused_scheme(database, 1, manager, 'Trace read into write using methods.in')
    assert manager.calls == saved['diagnostics']['ai_requests'] == 2
    assert len(saved['graph']['nodes']) == 60
    assert len(saved['graph']['edges']) == (120 if patch_valid else 119)
    assert saved['graph']['nodes'][-1]['description'] == graph['nodes'][-1]['description']
    assert saved['graph']['edges'][:119] == graph['edges']
    assert saved['graph']['explanation'] == graph['explanation']
    assert saved['coverage']['partial'] is not patch_valid
