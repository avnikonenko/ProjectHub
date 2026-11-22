"""Scope ranking uses saved, project-owned evidence within Scheme's limits."""
import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.integrations.codex import scheme_evidence as evidence
from backend.app.models import CodexSession, Note
from backend.tests.integrations.test_scheme_evidence import repository, setup


def test_scope_promotes_named_file_and_late_symbols_without_exporting_literals(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('def main():\n    return unrelated_process()\n')
    (repo / 'tests').mkdir()
    relative = 'tests/test_search_index.py'
    (repo / relative).write_text(
        ''.join(f'def helper_{number}():\n    return utility_{number}()\n' for number in range(90))
        + 'def rebuild_search_index():\n    return persist_search_index("PRIVATE_ARGUMENT_LITERAL")\n')
    engine = setup(tmp_path, repo)
    try:
        limits = (1800, 12_000, 140_000)
        general = evidence.collect_evidence(engine, 1, limits)
        assert all(source.get('path') != relative for source in general['sources'])

        def unexpected_inventory(*args, **kwargs):
            raise AssertionError('Focused inspection does not need the full dependency inventory.')

        monkeypatch.setattr(evidence, '_dependency_inventory', unexpected_inventory)
        scope = f'Trace rebuild_search_index in {relative}'
        result = evidence.collect_evidence(engine, 1, limits, scope=scope)
        source = next(source for source in result['sources'] if source.get('path') == relative)
        assert source['provenance'] == 'repository test source'
        assert 'rebuild_search_index' in source['text']
        assert 'persist_search_index' in source['text']
        assert 'PRIVATE_ARGUMENT_LITERAL' not in json.dumps(result)
        assert sum(len(source['text']) for source in result['sources']) <= limits[0]
        assert evidence.collect_evidence(engine, 1, limits, scope='  ' + scope)['diagnostics']['cache_hits'] == 1
        assert evidence.collect_evidence(engine, 1, limits, scope='Trace a different flow')['fingerprint'] != result['fingerprint']
    finally:
        engine.dispose()


def test_scope_recovers_older_record_passages_and_excludes_other_projects(tmp_path):
    engine = setup(tmp_path)
    relevant = 'An ingest checkpoint records source offsets before indexing.'
    with Session(engine) as db:
        db.add_all([
            Note(id=100, project_id=1, title='Early decisions', content=relevant),
            Note(id=101, project_id=2, title='Foreign decision', content='Foreign ingest checkpoint'),
            *[Note(id=number, project_id=1, title='Later note', content='Unrelated maintenance notes.')
              for number in range(102, 112)],
            CodexSession(id=100, project_id=1, title='First thread', thread_id='first'),
            CodexSession(id=101, project_id=2, title='Foreign thread', thread_id='foreign'),
        ])
        db.flush()
        db.execute(text("INSERT INTO codex_history_turns(project_id,session_id,session_title,turn_id,status,messages,content) "
                        "VALUES(:pid,:session,'Thread',:turn,'completed','[]',:content)"),
                   [{'pid': 1, 'session': 100, 'turn': 'first', 'content': relevant},
                    {'pid': 2, 'session': 101, 'turn': 'foreign', 'content': 'Foreign ingest checkpoint'},
                    *[{'pid': 1, 'session': 100, 'turn': str(number), 'content': 'Unrelated maintenance chat.'}
                      for number in range(15)]])
        db.commit()
    try:
        result = evidence.collect_evidence(engine, 1, scope='Show the ingest checkpoint before indexing')
        sources = {source['id']: source for source in result['sources']}
        for identity in ('note:100', 'session:100:turn:first'):
            assert sources[identity]['text'] == relevant
            assert sources[identity]['excerpt_spans'] == [{'start': 0, 'end': len(relevant)}]
        assert 'Foreign ingest checkpoint' not in json.dumps(result)
    finally:
        engine.dispose()


def test_scope_discloses_when_a_matching_record_exceeds_the_passage_read_bound(tmp_path):
    engine = setup(tmp_path)
    with Session(engine) as db:
        db.add(Note(id=100, project_id=1, title='Prior discussion',
                    content='Earlier discussion.\n\n' * 3200 + 'The unique checkpoint lives here.'))
        db.commit()
    try:
        result = evidence.collect_evidence(engine, 1, scope='unique checkpoint')
        assert all('The unique checkpoint lives here' not in source['text'] for source in result['sources'])
        assert any('inspection bound' in detail for detail in result['coverage']['limitations'])
    finally:
        engine.dispose()
