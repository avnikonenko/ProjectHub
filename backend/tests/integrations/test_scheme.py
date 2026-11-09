"""Structured Scheme graphs and persistence; Codex calls are mocked here."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import scheme
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import GenerationRevision, Project


def evidence():
    return {
        'sources': [
            {'id': 'file:api.py', 'path': 'api.py', 'label': 'API source', 'text': 'def serve(): pass',
             'provenance': 'observed in code', 'content_hash': 'abc'},
            {'id': 'discussion:1', 'label': 'Future design', 'text': 'Replace backend A with B',
             'provenance': 'mentioned in Codex conversation', 'content_hash': 'def'},
        ],
        'dependencies': [{'id': 'dep:fastapi', 'name': 'fastapi', 'constraint': '>=1',
                          'category': 'runtime', 'source_ids': ['file:api.py'], 'purpose': ''}],
        'tree': [{'path': 'api.py', 'type': 'file'}],
        'coverage': {'partial': True, 'limitations': ['Bounded code excerpts'], 'files_inspected': 1,
                     'conversation_excerpts': 1},
        'fingerprint': 'baseline', 'diagnostics': {'cache_hits': 1},
    }


def node(identifier='api', status='observed', refs=None):
    return {'id': identifier, 'label': 'API', 'description': 'Application interface', 'type': 'service',
            'status': status, 'source_ids': refs or ['file:api.py'], 'paths': ['api.py'] if not refs else []}


def output():
    return {
        'architecture': {
            'nodes': [node(), node('replacement', 'proposed', ['discussion:1'])],
            'edges': [{'source': 'api', 'target': 'replacement', 'label': 'planned replacement',
                       'type': 'replaces', 'status': 'uncertain',
                       'source_ids': ['file:api.py', 'discussion:1']}],
            'explanation': 'Code shows the API; discussion proposes a replacement. The relation is uncertain.',
        },
        'workflow': {'nodes': [], 'edges': [], 'explanation': 'Execution order is not established by this evidence.'},
        'dependencies': [{'id': 'dep:fastapi', 'purpose': 'Serves the application API.',
                          'source_ids': ['file:api.py']}],
    }


def test_graph_validates_conflicting_evidence_and_empty_workflow():
    architecture, workflow, dependencies = scheme.validate_output(json.dumps(output()), evidence())
    assert architecture['nodes'][1]['status'] == 'proposed'
    assert architecture['edges'][0]['status'] == 'uncertain'
    assert architecture['edges'][0]['source_ids'] == ['file:api.py', 'discussion:1']
    assert workflow['mermaid'] == ''
    assert dependencies[0]['name'] == 'fastapi'
    assert dependencies[0]['constraint'] == '>=1'
    assert dependencies[0]['purpose_source_ids'] == ['file:api.py']


@pytest.mark.parametrize('change,match', [
    (lambda result: result['architecture']['nodes'].append(deepcopy(result['architecture']['nodes'][0])), 'duplicate node'),
    (lambda result: result['architecture']['nodes'][0].update(id=' api '), 'node ID'),
    (lambda result: result['architecture']['edges'][0].update(target='missing'), 'missing endpoint'),
    (lambda result: result['architecture']['nodes'][0].update(source_ids=['project:foreign']), 'foreign evidence'),
    (lambda result: result['architecture']['nodes'][0].update(paths=['../../outside']), 'supporting evidence'),
    (lambda result: result['architecture']['nodes'][0].update(status='validated'), 'classification'),
    (lambda result: result['architecture']['nodes'][0].update(type='output'), 'classification'),
    (lambda result: result['workflow'].update(explanation=''), 'invalid text'),
    (lambda result: result['dependencies'][0].update(id='invented'), 'invented'),
    (lambda result: result['architecture']['edges'].append(deepcopy(result['architecture']['edges'][0])), 'duplicate relationships'),
    (lambda result: result['architecture'].update(mermaid='flowchart LR; dangerous'), 'invalid structure'),
])
def test_invalid_graph_is_rejected(change, match):
    result = output()
    change(result)
    with pytest.raises(CodexError, match=match):
        scheme.validate_output(json.dumps(result), evidence())


def test_deterministic_mermaid_escapes_labels_and_uses_fixed_syntax():
    result = output()
    result['architecture']['nodes'][0]['label'] = '"]\nclick x "javascript:alert(1)"<script>%%{init: {}}'
    graph = scheme.validate_output(json.dumps(result), evidence())[0]
    mermaid = graph['mermaid']
    assert 'javascript:' not in mermaid
    assert '<script>' not in mermaid
    assert '%%{' not in mermaid
    assert '" ]' not in mermaid
    assert '#34;' in mermaid and '#10;' in mermaid
    assert '(proposed)' not in mermaid  # Parentheses are escaped too.
    assert 'proposed' in mermaid and 'uncertain' in mermaid and '-.->' in mermaid
    graph['nodes'].reverse()
    assert scheme.render_mermaid(graph) == mermaid


def test_workflow_decisions_render_as_diamonds():
    result = output()
    stage = {**node('choose'), 'type': 'decision', 'label': 'Which backend?'}
    result['workflow']['nodes'] = [stage]
    graph = scheme.validate_output(json.dumps(result), evidence())[1]
    assert '{"Which backend#63; #40;observed#41;"}' in graph['mermaid']


def test_generation_calls_once_and_preserves_saved_snapshot_on_failure(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(name='One'), Project(name='Two')])
        db.commit()
    gathered = []

    def collect(engine, project_id):
        gathered.append(project_id)
        return evidence()

    monkeypatch.setattr(scheme, 'collect_evidence', collect)

    class Manager:
        calls = 0
        raw = json.dumps(output())
        generation_metadata = {'account': 'test', 'model': 'mock', 'effort': 'low'}

        def generate_brief(self, prompt, schema):
            self.calls += 1
            assert 'primary end-to-end' in prompt
            assert 'Do not substitute the workflow for generating this Scheme' in prompt
            assert 'static' in prompt and 'runtime correctness' in prompt
            assert 'Preserve conflicts' in prompt
            assert 'Do not use tools' in prompt
            assert schema['properties']['architecture']['properties']['nodes']['maxItems'] == 20
            assert schema['properties']['architecture']['properties']['nodes']['items']['properties']['source_ids']['items']['enum'] == ['file:api.py', 'discussion:1']
            return self.raw

    manager = Manager()
    saved = scheme.refresh_scheme(engine, 1, manager)
    assert gathered == [1] and manager.calls == 1
    assert saved['diagnostics']['ai_requests'] == 1
    assert saved['sources'] == evidence()['sources']
    assert saved['coverage']['partial']
    assert saved['generation'] == manager.generation_metadata
    with Session(engine) as db:
        assert scheme.read_scheme(db, 1) == saved
        assert scheme.read_scheme(db, 2) is None
    manager.raw = '{broken'
    with pytest.raises(CodexError, match='invalid JSON'):
        scheme.refresh_scheme(engine, 1, manager)
    assert manager.calls == 2  # No automatic repair/retry.
    with Session(engine) as db:
        assert scheme.read_scheme(db, 1) == saved
        revisions = list(db.scalars(select(GenerationRevision)))
        assert len(revisions) == 1 and revisions[0].kind == 'scheme'
        assert revisions[0].snapshot == saved
    engine.dispose()


def test_bounds_and_missing_evidence_do_not_call_codex(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='One'))
        db.commit()
    class Manager:
        def generate_brief(self, *args):
            pytest.fail('No Codex call should happen.')
    item = evidence()
    monkeypatch.setattr(scheme, 'collect_evidence', lambda *args: item)
    item['sources'] = []
    with pytest.raises(CodexError, match='No project evidence'):
        scheme.refresh_scheme(engine, 1, Manager())
    item['sources'] = evidence()['sources']
    item['sources'][0]['text'] = 'x' * scheme.MAX_INPUT_CHARS
    with pytest.raises(CodexError, match='input limit'):
        scheme.refresh_scheme(engine, 1, Manager())
    engine.dispose()
