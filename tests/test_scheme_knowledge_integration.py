"""Published Knowledge participates in Scheme evidence, budgets and freshness."""
from copy import deepcopy
import json

from sqlalchemy.orm import Session

from backend.app.models import ProjectKnowledge
from backend.app.integrations.codex import scheme_evidence as evidence
from backend.tests.integrations.test_scheme_evidence import repository, setup


def publish(engine, publication='publication-one'):
    value = {'project_id': 1, 'publication_id': publication, 'extraction_version': 'v1',
        'generated_at': 'then', 'fingerprint': 'same-source-fingerprint',
        'items': [{'id': 'knowledge:one', 'project_id': 1, 'statement': 'Use a pipeline.',
            'category': 'DECISION', 'design_status': 'AGREED', 'scope': 'Project',
            'rationale': '', 'source_state': 'valid', 'supersedes': [], 'superseded_by': [],
            'conflicts_with': [], 'evidence': [{'source_id': 'chatgpt:note:1:m:1:p:1',
                'role': 'user', 'excerpt': 'Use a pipeline.', 'revision': 'saved-revision'}]}],
        'sources': [{'id': 'chatgpt:note:1:m:1:p:1', 'note_id': 1, 'label': 'Original design'}]}
    with Session(engine) as db:
        row = db.get(ProjectKnowledge, 1)
        if row is None:
            db.add(ProjectKnowledge(project_id=1, snapshot=value))
        else:
            row.snapshot = value
        db.commit()


def assert_closed(result):
    sources = {source['id']: source for source in result['sources']}
    for source in sources.values():
        if source['provenance'] == 'published Knowledge interpretation':
            assert source['supporting_source_ids']
            assert all(ref in sources for ref in source['supporting_source_ids'])


def test_optional_knowledge_and_new_publication_invalidate_inspection_and_freshness(tmp_path):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('def main(): return process()\n')
    engine = setup(tmp_path, repo)
    try:
        without = evidence.collect_evidence(engine, 1)
        assert without['knowledge_context']['status'] == 'absent'
        publish(engine)
        first = evidence.collect_evidence(engine, 1)
        assert first['knowledge_context']['publication_id'] == 'publication-one'
        assert first['knowledge_context']['included_items'] == 1
        assert any(source.get('path') == 'main.py' for source in first['sources'])
        assert first['fingerprint'] != without['fingerprint']
        assert_closed(first)
        replay = evidence.collect_evidence(engine, 1)
        assert replay['diagnostics']['cache_hits'] == 1
        publish(engine, 'publication-two')
        changed = evidence.collect_evidence(engine, 1)
        assert changed['diagnostics']['cache_hits'] == 0
        assert first['fingerprint'] != changed['fingerprint']
        assert changed['knowledge_context']['publication_id'] == 'publication-two'
        assert_closed(changed)
        foreign = evidence.collect_evidence(engine, 2)
        assert foreign['knowledge_context']['status'] == 'absent'
    finally:
        engine.dispose()


def test_text_budget_does_not_orphan_knowledge_or_hide_publication_identity(tmp_path, monkeypatch):
    engine = setup(tmp_path)
    try:
        publish(engine)
        monkeypatch.setattr(evidence, 'scheme_inspection_limits', lambda: (70, 2 * 1024 * 1024, 140_000))
        first = evidence.collect_evidence(engine, 1)
        assert first['knowledge_context']['publication_id'] == 'publication-one'
        assert first['knowledge_context']['included_items'] == 0
        assert first['knowledge_context']['omitted_items'] == 1
        assert_closed(first)
        publish(engine, 'publication-two')
        second = evidence.collect_evidence(engine, 1)
        assert second['fingerprint'] != first['fingerprint']
    finally:
        engine.dispose()


def test_serialized_budget_removes_interpretation_when_original_is_trimmed():
    derived = evidence._source('derived', 'Interpretation', 'Agreed design',
        'published Knowledge interpretation', supporting_source_ids=['original'])
    original = evidence._source('original', 'Original quote', 'Original evidence. ' * 100,
        'published Knowledge original evidence')
    result = {'sources': [derived, original], 'dependencies': [], 'tree': [],
        'knowledge_context': {'available_items': 1, 'included_items': 1, 'publication_id': 'publication-one'},
        'coverage': {'limitations': [], 'partial': False, 'omitted_reason_counts': {}}}
    evidence._bound_package(result, len(json.dumps(result)) - 300)
    assert_closed(result)
    assert result['knowledge_context']['publication_id'] == 'publication-one'
    assert result['knowledge_context']['included_items'] == 0
    assert result['knowledge_context']['omitted_items'] == 1
    assert 'derived' not in {source['id'] for source in result['sources']}


def test_original_closure_is_enforced_even_below_package_limit():
    result = {'sources': [evidence._source('derived', 'Interpretation', 'Agreed design',
            'published Knowledge interpretation', supporting_source_ids=['missing'])],
        'dependencies': [], 'tree': [], 'knowledge_context': {'available_items': 1, 'included_items': 1},
        'coverage': {'limitations': [], 'partial': False, 'omitted_reason_counts': {}}}
    before = deepcopy(result)
    evidence._bound_package(result)
    assert result['sources'] == [] and before['sources']
    assert result['coverage']['partial']
    assert result['knowledge_context']['included_items'] == 0


def test_scheme_get_marks_new_publication_changed_without_generating_or_repairing_saved_result(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.integrations.codex import router as routes
    from backend.app.models import CodexSession, ProjectScheme

    repo = repository(tmp_path)
    (repo / 'main.py').write_text('def main(): return process()\n')
    engine = setup(tmp_path, repo)
    try:
        with Session(engine) as db:
            session = db.get(CodexSession, 1)
            session.history_indexed_activity = session.codex_activity_at = 'now'
            db.commit()
        publish(engine)
        saved = evidence.collect_evidence(engine, 1)
        with Session(engine) as db:
            db.add(ProjectScheme(project_id=1, snapshot={**saved, 'generated_at': 'then'}))
            db.commit()

        def no_model(*args, **kwargs):
            raise AssertionError('Reading Knowledge context must not invoke Codex')

        monkeypatch.setattr(routes, 'manager_for', no_model)
        app = FastAPI()
        app.state.engine = engine
        app.include_router(routes.router)
        with TestClient(app) as client:
            first = client.get('/api/projects/1/codex/scheme').json()
            assert first['freshness']['state'] == 'unchanged'
            publish(engine, 'publication-two')
            second = client.get('/api/projects/1/codex/scheme').json()
            assert second['freshness']['state'] == 'changed'
            assert second['snapshot']['knowledge_context']['publication_id'] == 'publication-one'
            assert second['snapshot']['fingerprint'] == first['snapshot']['fingerprint']
    finally:
        engine.dispose()
