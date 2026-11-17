"""Scheme citation contracts; all generation uses a deterministic mocked manager."""
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import scheme
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import GenerationRevision, Project
from backend.tests.integrations.test_scheme import evidence, output


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


def test_duplicate_normalization_preserves_path_support_checks():
    result = output()
    result['architecture']['nodes'][0]['source_ids'] = ['discussion:1', 'discussion:1']
    with pytest.raises(CodexError, match='path without supporting evidence'):
        scheme.validate_output(json.dumps(result), evidence())


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
            return json.dumps(self.result)

    manager = Manager()
    manager.result['architecture']['nodes'][0]['source_ids'] *= 2
    saved = scheme.refresh_scheme(engine, 1, manager)
    assert saved['architecture']['nodes'][0]['source_ids'] == ['file:api.py']
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
