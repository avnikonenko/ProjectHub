"""Real SQLite regressions for metadata projection and project search work."""
import gc
import json
import tracemalloc

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.app.models import Project, ProjectKnowledge
from backend.app.integrations.codex.revisions import list_revisions
from backend.app.project_search import project_search


@pytest.fixture
def query_app(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECTHUB_HISTORY_AUTO_SYNC', 'false')
    app = create_app(tmp_path / 'data')
    with Session(app.state.engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')])
        db.commit()
    with TestClient(app) as client:
        yield client, app.state.engine
    app.state.engine.dispose()


def test_revision_listing_does_not_deserialize_snapshot_bodies(query_app):
    _, engine = query_app
    generation = {'model': 'synthetic', 'nested': {'verified': False}, 'labels': ['one', 'two']}
    payload = json.dumps({'generation': generation, 'body': 'x' * (2 * 1024 * 1024)})
    with engine.begin() as connection:
        connection.execute(text('INSERT INTO generation_revisions(project_id,kind,generated_at,snapshot) '
                                'VALUES(1,\'brief\',:date,:snapshot)'),
                           [{'date': f'2026-11-{i:02}', 'snapshot': payload} for i in range(1, 12)])
    del payload
    gc.collect()
    tracemalloc.start()
    try:
        with Session(engine) as db:
            result = list_revisions(db, 1, 'brief', 0)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(result['results']) == 10
    assert result['next_offset'] == 10
    assert all(row['generation'] == generation for row in result['results'])
    # Eleven 2 MiB bodies previously produced >44 MiB of transient allocations.
    # The response contains only dates/IDs and the small nested metadata above.
    assert peak < 3 * 1024 * 1024


def test_revision_listing_preserves_missing_metadata_pagination_and_project_isolation(query_app):
    client, engine = query_app
    with engine.begin() as connection:
        connection.execute(text('INSERT INTO generation_revisions(project_id,kind,generated_at,snapshot) '
                                'VALUES(:pid,:kind,:date,:snapshot)'), [
            {'pid': pid, 'kind': kind, 'date': f'date-{i}', 'snapshot': '{}'}
            for pid, kind, count in ((1, 'brief', 12), (1, 'qa', 1), (2, 'brief', 1))
            for i in range(count)])
    first = client.get('/api/projects/1/codex/revisions?kind=brief').json()
    second = client.get('/api/projects/1/codex/revisions?kind=brief&offset=10').json()
    assert len(first['results']) == 10 and first['next_offset'] == 10
    assert len(second['results']) == 2 and second['next_offset'] is None
    assert all(row['generation'] is None for row in first['results'])
    assert {row['id'] for page in (first, second) for row in page['results']} == set(range(1, 13))


def test_knowledge_label_search_keeps_first_valid_source_and_project_guards(query_app):
    client, engine = query_app
    with Session(engine) as db:
        db.add(ProjectKnowledge(project_id=1, snapshot={
            'project_id': 1, 'publication_id': 'publication-one',
            'items': [
                {'id': 'valid', 'statement': 'Original choice', 'evidence': [
                    {'source_id': 'source', 'excerpt': 'Retained excerpt', 'label': 'Local evidence'}]},
                {'id': 'foreign', 'project_id': 2, 'statement': 'Forbidden foreign record', 'evidence': []},
            ],
            'sources': [
                {'id': 'source', 'project_id': 2, 'label': 'Forbidden foreign label'},
                {'id': 'source', 'project_id': 1, 'label': 'Ätom benchmark'},
                {'id': 'source', 'label': 'Later duplicate label'},
            ],
        }))
        db.commit()
    def hits(query):
        response = client.get('/api/projects/1/search/all', params={'kind': 'knowledge', 'q': query})
        assert response.status_code == 200, response.text
        return response.json()['groups'][0]['results']
    assert [row['id'] for row in hits('ätom benchmark')] == ['knowledge:valid']
    assert hits('retained local')
    assert not hits('forbidden')
    assert not hits('later duplicate')
    assert hits('benchmark')[0]['href'] == '#knowledge?project=1&item=valid&publication=publication-one'


def test_knowledge_source_lookup_work_scales_with_records_not_source_cross_product(query_app):
    _, engine = query_app
    ticks = []
    for count in (200, 800):
        with Session(engine) as db:
            previous = db.get(ProjectKnowledge, 1)
            snapshot = {
                'project_id': 1, 'publication_id': 'bounded-search',
                'items': [{'id': f'item:{i}', 'statement': f'Choice {i}',
                           'evidence': [{'source_id': f'source:{i}', 'excerpt': 'Original evidence'}]}
                          for i in range(count)],
                'sources': [{'id': f'source:{i}', 'label': f'Source {i}'} for i in range(count)],
            }
            if previous is None:
                db.add(ProjectKnowledge(project_id=1, snapshot=snapshot))
            else:
                previous.snapshot = snapshot
            db.commit()
        with Session(engine) as db:
            connection = db.connection().connection.driver_connection
            operations = [0]
            def progress():
                operations[0] += 1
                return 0
            connection.set_progress_handler(progress, 100)
            try:
                result = project_search(db, 1, 'absentword', 'knowledge')
            finally:
                connection.set_progress_handler(None, 0)
            assert result['groups'][0]['results'] == []
            ticks.append(operations[0])
    # Four times the records must not cause a sixteen-fold source cross-product.
    # Count SQLite VM work, rather than asserting a machine-dependent wall time.
    assert ticks[1] < ticks[0] * 7
