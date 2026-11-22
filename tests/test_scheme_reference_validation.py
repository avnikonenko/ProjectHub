"""Scheme citation contracts; all generation uses a deterministic mocked manager."""
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import scheme
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import GenerationRevision, Project
from backend.tests.integrations.test_scheme import evidence, knowledge_evidence, output


TARGETS = [
    ('architecture', 'nodes', 'architecture.nodes[0].source_ids'),
    ('architecture', 'edges', 'architecture.edges[0].source_ids'),
    (None, 'dependencies', 'dependencies[0].source_ids'),
]


def _target(result, graph, collection):
    return (result[graph] if graph else result)[collection][0]


@pytest.mark.parametrize('graph,collection,location', TARGETS)
@pytest.mark.parametrize('refs,message', [
    (None, 'non-list'),
    ('file:api.py', 'non-list'),
    ([], '1–6'),
    (['file:api.py'] * 7, '1–6'),
    ([None], 'non-string'),
    ([{}], 'non-string'),
    (['unknown-sensitive-reference'], 'missing or foreign evidence'),
    (['file:api.py', 'file:api.py', 'unknown-sensitive-reference'], 'missing or foreign evidence'),
    ([' file:api.py '], 'missing or foreign evidence'),
])
def test_invalid_references_are_precise_and_located(graph, collection, location, refs, message):
    result = output()
    _target(result, graph, collection)['source_ids'] = refs
    with pytest.raises(CodexError) as caught:
        scheme.validate_output(json.dumps(result), evidence())
    assert message in str(caught.value)
    assert location in str(caught.value)
    assert 'unknown-sensitive-reference' not in str(caught.value)


@pytest.mark.parametrize('graph,collection,location', TARGETS)
def test_valid_duplicate_references_are_normalized_in_first_seen_order(graph, collection, location):
    result = output()
    refs = ['discussion:1', 'file:api.py', 'discussion:1', 'file:api.py']
    _target(result, graph, collection)['source_ids'] = refs
    architecture, workflow, dependencies = scheme.validate_output(json.dumps(result), evidence())
    normalized = (dependencies[0]['purpose_source_ids'] if graph is None
                  else architecture[collection][0]['source_ids'])
    assert normalized == ['discussion:1', 'file:api.py']
    assert architecture['nodes'][0]['paths'] == ['api.py']
    assert workflow['nodes'] == []


def test_node_paths_come_only_from_cited_sources():
    result = output()
    inputs = evidence()
    inputs['sources'].append({**inputs['sources'][0], 'id': 'file:api.py:other-excerpt'})
    result['architecture']['nodes'][0]['source_ids'] = ['file:api.py', 'discussion:1', 'file:api.py:other-excerpt']
    architecture, _, _ = scheme.validate_output(json.dumps(result), inputs)
    assert [node['paths'] for node in architecture['nodes']] == [['api.py'], []]


def test_six_references_are_allowed_but_seven_are_not_collapsed_before_validation():
    assert scheme._references(['file:api.py'] * 6, {'file:api.py'}) == ['file:api.py']
    with pytest.raises(CodexError, match='1–6'):
        scheme._references(['file:api.py'] * 7, {'file:api.py'})


def test_refresh_saves_normalized_references_and_preserves_snapshot_after_foreign_reference(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(name='One'), Project(name='Two')])
        db.commit()
    monkeypatch.setattr(scheme, 'collect_evidence', lambda *args: evidence())

    class Manager:
        calls = 0
        generation_metadata = {'model': 'mock'}
        result = output()

        def generate_brief(self, prompt, schema):
            self.calls += 1
            assert '1–6 distinct IDs exactly from the top-level sources list' in prompt
            assert 'citations embedded in source text' in prompt
            assert 'dependency IDs' in prompt
            for kind in ('architecture', 'workflow'):
                node_schema = schema['properties'][kind]['properties']['nodes']['items']
                assert 'paths' not in node_schema['properties']
                assert 'paths' not in node_schema['required']
            return json.dumps(self.result)

    manager = Manager()
    manager.result['architecture']['nodes'][0]['source_ids'] *= 2
    saved = scheme.refresh_scheme(engine, 1, manager)
    assert saved['architecture']['nodes'][0]['source_ids'] == ['file:api.py']
    assert saved['architecture']['nodes'][0]['paths'] == ['api.py']
    manager.result['architecture']['nodes'][0]['source_ids'].append('note:foreign-project')
    with pytest.raises(CodexError, match='missing or foreign evidence'):
        scheme.refresh_scheme(engine, 1, manager)
    assert manager.calls == 2
    with Session(engine) as db:
        assert scheme.read_scheme(db, 1) == saved
        assert scheme.read_scheme(db, 2) is None
        revisions = list(db.scalars(select(GenerationRevision)))
        assert len(revisions) == 1
        assert revisions[0].project_id == 1
        assert revisions[0].snapshot == saved
    engine.dispose()


@pytest.fixture
def knowledge_scheme(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='One'))
        db.commit()
    inputs = knowledge_evidence()
    monkeypatch.setattr(scheme, 'collect_evidence', lambda *args: inputs)
    try:
        yield engine, inputs
    finally:
        engine.dispose()


class _ScriptedManager:
    generation_metadata = {'model': 'mock'}

    def __init__(self, *outputs):
        self.outputs = outputs
        self.prompts = []

    def generate_brief(self, prompt, schema):
        index = len(self.prompts)
        self.prompts.append(prompt)
        assert index < len(self.outputs), 'Unexpected extra model request'
        return json.dumps(self.outputs[index])


@pytest.mark.parametrize('graph,collection,location', TARGETS)
def test_refresh_completes_cited_knowledge_sources_without_another_request(
    knowledge_scheme, graph, collection, location,
):
    engine, inputs = knowledge_scheme
    inputs['sources'].append({
        **inputs['sources'][-1], 'id': 'knowledge:item:related',
        'supporting_source_ids': ['knowledge:original:proposal'],
    })
    draft = output()
    refs = ['file:api.py', 'knowledge:item:choice', 'knowledge:original:acceptance',
            'knowledge:item:related']
    _target(draft, graph, collection)['source_ids'] = refs
    manager = _ScriptedManager(draft)

    saved = scheme.refresh_scheme(engine, 1, manager)

    expected = [*refs, 'knowledge:original:proposal']
    actual = (saved['dependencies'][0]['purpose_source_ids'] if graph is None
              else saved[graph][collection][0]['source_ids'])
    assert actual == expected, location
    assert saved['diagnostics']['ai_requests'] == len(manager.prompts) == 1
    with Session(engine) as db:
        assert scheme.read_scheme(db, 1) == saved


def _overflowing_knowledge_edge(inputs):
    extra = ['knowledge:original:extra-1', 'knowledge:original:extra-2', 'knowledge:original:extra-3']
    original = next(source for source in inputs['sources'] if source['id'] == 'knowledge:original:proposal')
    inputs['sources'].extend({**original, 'id': identifier} for identifier in extra)
    inputs['sources'].append({
        **next(source for source in inputs['sources'] if source['id'] == 'knowledge:item:choice'),
        'id': 'knowledge:item:related',
        'supporting_source_ids': ['knowledge:original:acceptance', *extra],
    })
    draft = output()
    draft['architecture']['edges'][0]['source_ids'] = ['knowledge:item:choice', 'knowledge:item:related']
    return draft


def test_refresh_retries_once_when_knowledge_support_cannot_fit(knowledge_scheme):
    engine, inputs = knowledge_scheme
    draft = _overflowing_knowledge_edge(inputs)
    corrected = output()
    corrected['architecture']['edges'][0]['source_ids'] = ['knowledge:item:related']
    manager = _ScriptedManager(draft, corrected)

    saved = scheme.refresh_scheme(engine, 1, manager)

    assert saved['diagnostics']['ai_requests'] == len(manager.prompts) == 2
    assert 'architecture.edges[0].source_ids' in manager.prompts[1]
    assert 'original evidence' in manager.prompts[1]
    assert saved['architecture']['edges'][0]['source_ids'] == [
        'knowledge:item:related', 'knowledge:original:acceptance',
        'knowledge:original:extra-1', 'knowledge:original:extra-2', 'knowledge:original:extra-3',
    ]


def test_repeated_knowledge_source_overflow_preserves_saved_scheme(knowledge_scheme):
    engine, inputs = knowledge_scheme
    draft = _overflowing_knowledge_edge(inputs)
    saved = scheme.refresh_scheme(engine, 1, _ScriptedManager(output()))
    manager = _ScriptedManager(draft, draft)

    with pytest.raises(CodexError, match=r'architecture\.edges\[0\]\.source_ids'):
        scheme.refresh_scheme(engine, 1, manager)

    assert len(manager.prompts) == 2
    with Session(engine) as db:
        assert scheme.read_scheme(db, 1) == saved
        assert [revision.snapshot for revision in db.scalars(select(GenerationRevision))] == [saved]


def test_refresh_never_repairs_unavailable_knowledge_support(knowledge_scheme):
    engine, inputs = knowledge_scheme
    inputs['sources'][-1]['supporting_source_ids'] = ['note:foreign-project']
    draft = output()
    draft['architecture']['edges'][0]['source_ids'] = ['knowledge:item:choice']
    manager = _ScriptedManager(draft)

    with pytest.raises(CodexError, match='Knowledge without valid original evidence'):
        scheme.refresh_scheme(engine, 1, manager)

    assert len(manager.prompts) == 1
    with Session(engine) as db:
        assert scheme.read_scheme(db, 1) is None
        assert db.scalar(select(GenerationRevision)) is None
