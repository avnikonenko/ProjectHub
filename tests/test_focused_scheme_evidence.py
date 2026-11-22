"""Scope ranking uses saved, project-owned evidence within Scheme's limits."""
import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.integrations.codex import scheme_evidence as evidence
from backend.app.models import CodexSession, Note
from backend.tests.integrations.test_scheme_evidence import repository, setup


def test_scope_supplies_registered_search_routes_and_transcript_link(tmp_path):
    repo = repository(tmp_path)
    paths = ('backend/app/main.py', 'backend/app/project_search.py', 'frontend/src/historyLinks.ts')
    for path in paths:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((Path(__file__).resolve().parents[1] / path).read_bytes())
    engine = setup(tmp_path, repo)
    try:
        scope = 'Show how imported conversations become searchable, from saving the transcript to opening a result.'
        result = evidence.collect_evidence(engine, 1, scope=scope)
        excerpts = {source.get('path'): source['text'] for source in result['sources']}
        assert 'HTTP route GET /api/projects/{project_id}/history/search' in excerpts[paths[0]]
        assert 'HTTP route GET /api/projects/{project_id}/search/all' in excerpts[paths[0]]
        assert 'project_search builds navigation link #history?' in excerpts[paths[1]]
        assert 'Client navigation readHistoryLink checks #history?' in excerpts[paths[2]]
    finally:
        engine.dispose()


def test_client_navigation_evidence_uses_structural_fragments_only():
    source = (
        "// return '#from-comment?'\n"
        "function readHistoryLink(hash) { return hash.startsWith('#history?'); }\n"
        "function buildLink() { return `#history?${privateQuery}`; }\n"
        "function privateLink() { return '#history?token=PRIVATE_VALUE'; }\n"
    )
    output = evidence._structural_excerpt(source, 'historyLinks.ts', focus=evidence._EvidenceScope('History link').score)
    assert 'readHistoryLink checks #history?' in output
    assert 'buildLink returns #history?' in output
    assert 'PRIVATE_VALUE' not in output and 'from-comment' not in output


def test_search_scope_supplies_projecthub_indexing_migration(tmp_path):
    repo = repository(tmp_path)
    (repo / 'main.py').write_text('def save_conversation():\n    return commit()\n')
    path = 'migrations/versions/0001_initial.py'
    target = repo / path
    target.parent.mkdir(parents=True)
    target.write_bytes((Path(__file__).resolve().parents[1] / path).read_bytes())
    engine = setup(tmp_path, repo)
    try:
        scope = 'Show how imported conversations become searchable, from saving the transcript to opening a result.'
        result = evidence.collect_evidence(engine, 1, scope=scope)
        source = next(source for source in result['sources'] if source.get('path') == path)
        assert 'CREATE VIRTUAL TABLE search_index' in source['text']
        assert 'fts5' in source['text'].lower()
        assert all(f'notes_search_{action}' in source['text'] for action in ('insert', 'update', 'delete'))
        assert any('search_index' in line and 'notes_search_insert' in line for line in source['text'].splitlines())
        general = evidence.collect_evidence(engine, 1)
        assert all(source.get('path') != path for source in general['sources'])
    finally:
        engine.dispose()


def test_scope_matches_common_word_families_and_prefers_exact_words():
    scope = evidence._EvidenceScope('Show how cached search is retrieved')
    assert scope.score('cache retrieval') > scope.score('search') > 0
    assert scope.score('cached and retrieved') > scope.score('cache retrieval')
    assert scope.score('cache caches caching') == scope.score('cache')
    assert scope.score('cachalot') == 0
    assert evidence._EvidenceScope('validate policies').score('policy validation') > 0


def test_scope_finds_an_older_record_using_word_variants(tmp_path):
    engine = setup(tmp_path)
    relevant = 'Cache retrieval begins with a project-local lookup.'
    try:
        with Session(engine) as db:
            db.add(Note(id=100, project_id=1, title='Earlier decision', content=relevant))
            db.add_all(Note(id=note_id, project_id=1, title='Recent', content='Unrelated housekeeping.')
                       for note_id in range(101, 111))
            db.commit()
        result = evidence.collect_evidence(engine, 1, scope='Show how cached data is retrieved.')
        source = next(source for source in result['sources'] if source['id'] == 'note:100')
        assert source['text'] == relevant
        assert source['excerpt_spans'] == [{'start': 0, 'end': len(relevant)}]
    finally:
        engine.dispose()


def test_focused_python_excerpt_preserves_startup_and_predicates_without_literals():
    scope = evidence._EvidenceScope('Trace startup and when the queue is rebuilt.')
    source = (
        'from worker import initialize\n'
        'def rebuild_index(queue):\n'
        '    if queue:\n'
        '        dispatch_index(queue)\n'
        '    else:\n'
        '        report_empty()\n'
        '    if password == "PRIVATE_PREDICATE_LITERAL":\n'
        '        save_state()\n'
        'if __name__ == "__main__":\n'
        '    initialize()\n'
    )
    focused = evidence._structural_excerpt(source, 'main.py', focus=scope.score)
    assert 'module level within if __name__ == "__main__" uses call syntax: initialize' in focused
    assert 'rebuild_index within if queue uses call syntax: dispatch_index' in focused
    assert 'rebuild_index within else of if queue uses call syntax: report_empty' in focused
    assert 'if password == <literal>' in focused
    assert 'PRIVATE_PREDICATE_LITERAL' not in focused
    inverse = evidence._structural_excerpt(source.replace('if queue:', 'if not queue:'), 'main.py', focus=scope.score)
    assert 'rebuild_index within if not (queue) uses call syntax: dispatch_index' in inverse
    assert 'module level' not in evidence._structural_excerpt(source, 'main.py')


def test_focused_typescript_excerpt_keeps_calls_with_their_own_scope():
    scope = evidence._EvidenceScope('Trace flushQueue and registration.')
    source = ('function status() { return readStatus(); }\n'
              'const flushQueue = () => { persistBatch(); };\n'
              'registerHandler(flushQueue);\n')
    focused = evidence._structural_excerpt(source, 'queue.ts', focus=scope.score)
    assert 'status lexical call syntax: readStatus' in focused
    assert 'flushQueue lexical call syntax: persistBatch' in focused
    assert 'module lexical call syntax: registerHandler' in focused
    assert 'status lexical call syntax: readStatus, persistBatch' not in focused


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


def test_scope_reads_matching_note_passage_after_the_original_prefix(tmp_path):
    engine = setup(tmp_path)
    prefix = 'Earlier discussion.\n\n' * 3200
    match = 'The unique checkpoint lives here.'
    content = prefix + match
    with Session(engine) as db:
        db.add(Note(id=100, project_id=1, title='Prior discussion',
                    content=content))
        db.commit()
    try:
        result = evidence.collect_evidence(engine, 1, scope='unique checkpoint')
        source = next(source for source in result['sources'] if source['id'] == 'note:100')
        assert match in source['text']
        assert source['inspected_start'] > 0
        assert all(content[span['start']:span['end']] in source['text'] for span in source['excerpt_spans'])
        assert any('bounded windows' in detail for detail in result['coverage']['limitations'])
    finally:
        engine.dispose()
