"""Archive traversal, bounded generation, and cache persistence on actual SQLite."""
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, CodexSession, ProjectHistoryBatch
from backend.app.integrations.codex.archive import archive_history, summarize_evidence, BATCH_CHARS
from backend.app.integrations.codex.protocol import CodexError


def turn(number, text='Recorded conclusion', status='completed'):
    return {'id': str(number), 'status': status, 'messages': [{'role': 'assistant', 'text': text}]}


class FakeCodex:
    def __init__(self):
        self.turns = {'a': [turn(1)], 'b': [turn(2)]}
        self.calls, self.inputs = [], []
        self.closed = False
        self.foreign = self.repeat_cursor = self.fail = False

    def _new_read_client(self):
        self.closed = False
        return self

    def close(self):
        self.closed = True

    def history(self, session, cursor, *, client, archive):
        assert client is self and archive is True
        self.calls.append((session.thread_id, cursor))
        offset = int(cursor or 0)
        values = self.turns[session.thread_id]
        return {'turns': values[offset:offset + 5],
                'next_cursor': 'same' if self.repeat_cursor else
                               str(offset + 5) if offset + 5 < len(values) else None}

    def generate_brief(self, prompt, output_schema):
        if self.fail:
            raise CodexError('Generation failed')
        data = json.loads(prompt.split('\n', 1)[1])
        self.inputs.append(data)
        assert len(json.dumps(data, ensure_ascii=False)) < BATCH_CHARS + 100
        ids = ([s['id'] for s in data['sources']] if 'sources' in data else
               [s for item in data['evidence'] for s in item['source_ids']])
        ids = list(dict.fromkeys(ids))
        allowed = output_schema['properties']['evidence']['items']['properties']['source_ids']['items']['enum']
        assert set(allowed) == set(ids)
        limit = output_schema['properties']['evidence']['maxItems']
        return json.dumps({'evidence': [{'text': 'Saved outcome ' + str(i),
                                        'source_ids': ['foreign' if self.foreign else source]}
                                       for i, source in enumerate(ids[:limit])]})


@pytest.fixture
def env(tmp_path):
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add_all([Project(id=1, name='A'), Project(id=2, name='B')]); db.flush()
        db.add_all([CodexSession(id=1, project_id=1, thread_id='a'),
                    CodexSession(id=2, project_id=2, thread_id='b')]); db.commit()
    yield engine, FakeCodex()
    engine.dispose()


def test_archive_scopes_sessions_traverses_every_page_and_skips_active(env):
    engine, manager = env
    manager.turns['a'] = [turn(i) for i in range(13)] + [turn(14, 'Pending', 'inProgress')]
    evidence, coverage = archive_history(engine, 1, manager)
    assert manager.calls == [('a', None), ('a', '5'), ('a', '10')]
    assert manager.closed
    sources = [s for payload in manager.inputs for s in payload['sources']]
    assert len(sources) == 13 and all(s['session_id'] == 1 for s in sources)
    assert all(s['turn_id'] != '14' for s in sources)
    assert any('Skipped 1 in-progress' in item for item in coverage)
    assert evidence and all(s['turn_status'] == 'completed' for e in evidence for s in e['sources'])


def test_archive_splits_without_losing_tail_and_reuses_earlier_batches(env):
    engine, manager = env
    text = 'x' * 15_000 + 'THE CONCLUSION'
    manager.turns['a'] = [turn(i, text) for i in range(6)]
    first, _ = archive_history(engine, 1, manager)
    generated = len(manager.inputs)
    source_text = ''.join(source['text'] for payload in manager.inputs for source in payload['sources'])
    assert source_text.count('THE CONCLUSION') == 6
    assert generated > 2
    second, coverage = archive_history(engine, 1, manager)
    assert first == second and len(manager.inputs) == generated
    assert any(f'{generated} reused from cache, 0 generated' in item for item in coverage)
    manager.turns['a'].append(turn(7, 'A newly completed outcome'))
    third, coverage = archive_history(engine, 1, manager)
    assert third and 0 < len(manager.inputs) - generated < generated
    assert manager.closed


def test_archive_rejects_foreign_citations_and_does_not_cache_failure(env):
    engine, manager = env
    manager.foreign = True
    with pytest.raises(CodexError, match='foreign'):
        archive_history(engine, 1, manager)
    assert manager.closed
    with Session(engine) as db:
        assert list(db.scalars(select(ProjectHistoryBatch))) == []
    manager.foreign, manager.fail = False, True
    with pytest.raises(CodexError, match='Generation failed'):
        archive_history(engine, 1, manager)
    assert manager.closed


def test_archive_repeated_cursor_fails_explicitly(env):
    engine, manager = env
    manager.repeat_cursor = True
    original = manager.history
    def repeated(session, cursor, **kwargs):
        return original(session, None, **kwargs)
    manager.history = repeated
    with pytest.raises(CodexError, match='repeated pagination cursor'):
        archive_history(engine, 1, manager)
    assert len(manager.calls) == 2 and manager.closed


def test_archive_missing_project_does_not_read_or_generate(env):
    engine, manager = env
    with pytest.raises(CodexError, match='Project not found'):
        archive_history(engine, 99, manager)
    assert manager.calls == manager.inputs == []


def test_recursive_reduction_preserves_original_sources_and_cache(env):
    engine, manager = env
    evidence = [{'text': 'Fact ' + str(i), 'sources': [
        {'id': f'session:1:turn:{i}:part:1', 'label': 'Saved turn', 'text': 'Original excerpt ' + str(i),
         'session_id': 1, 'turn_id': str(i), 'turn_status': 'completed'}]} for i in range(90)]
    reduced = summarize_evidence(engine, 1, manager, evidence)
    assert 0 < len(reduced) <= 24
    original = {s['id']: s for e in evidence for s in e['sources']}
    assert all(s == original[s['id']] for e in reduced for s in e['sources'])
    generated = len(manager.inputs)
    assert summarize_evidence(engine, 1, manager, evidence) == reduced
    assert len(manager.inputs) == generated


@pytest.mark.parametrize('count', [18, 40])
def test_reduction_packs_long_recorded_evidence_by_serialized_size(env, count):
    engine, manager = env
    evidence = [{'text': 'Recorded knowledge ' + 'x' * 3000, 'sources': [
        {'id': f'decision:{i}', 'label': f'Accepted decision #{i}', 'text': 'Original decision',
         'decision_id': i}]} for i in range(count)]
    reduced = summarize_evidence(engine, 1, manager, evidence)
    assert len(reduced) < count
    assert all('source_labels' in item for payload in manager.inputs for item in payload['evidence'])
    assert all(s['label'].startswith('Accepted decision') for e in reduced for s in e['sources'])


def test_archive_cache_respects_project_ai_instructions(env):
    from backend.app.integrations.codex.ai_instructions import use_project_instructions

    engine, manager = env
    archive_history(engine, 1, manager)
    assert len(manager.inputs) == 1
    with use_project_instructions('Summarize in British English.'):
        archive_history(engine, 1, manager)
        assert len(manager.inputs) == 2
        archive_history(engine, 1, manager)
        assert len(manager.inputs) == 2
    archive_history(engine, 1, manager)
    assert len(manager.inputs) == 2


class IndexedCodex(FakeCodex):
    def __init__(self):
        super().__init__()
        self.activity = 1_700_000_000
        self.metadata_calls = []

    def request(self, method, params):
        assert method == 'thread/read' and params['includeTurns'] is False
        self.metadata_calls.append(params['threadId'])
        return {'thread': {'id': params['threadId'], 'status': {'type': 'idle'},
                           'updatedAt': self.activity}}


def index_session(engine, manager, project_id=1):
    from backend.app.integrations.codex.history_search import index_history

    with Session(engine) as db:
        session = db.scalar(select(CodexSession).where(CodexSession.project_id == project_id))
        session.codex_activity_at = datetime.fromtimestamp(manager.activity, timezone.utc).isoformat()
        db.commit()
    return index_history(engine, project_id, manager)


def test_archive_reuses_fresh_session_index_without_rereading_turns(env):
    engine, _ = env
    manager = IndexedCodex()
    manager.turns['a'] = [turn(i, 'Latest saved result') for i in range(7)]
    indexed = index_session(engine, manager)
    manager.calls.clear()
    evidence, coverage = archive_history(engine, 1, manager)
    assert manager.calls == []
    assert manager.metadata_calls == ['a']
    assert all(source['session_id'] == 1 for item in evidence for source in item['sources'])
    assert any('1 session indexes reused' in item for item in coverage)
    assert any(indexed['indexed_at'] in item for item in coverage)
    first_inputs = list(manager.inputs)
    second, _ = archive_history(engine, 1, manager)
    assert second == evidence
    assert manager.inputs == first_inputs
    assert manager.calls == []


def test_archive_does_not_trust_project_index_timestamp_for_an_older_session(env):
    engine, _ = env
    manager = IndexedCodex()
    index_session(engine, manager)
    manager.activity += 60
    manager.turns['a'] = [turn(2, 'New live evidence')]
    with engine.begin() as connection:
        connection.execute(text("UPDATE codex_history_index SET indexed_at='2099-01-01T00:00:00+00:00'"))
    manager.calls.clear()
    evidence, coverage = archive_history(engine, 1, manager)
    assert manager.calls == [('a', None)]
    assert any('New live evidence' in source['text'] for item in evidence for source in item['sources'])
    assert any('0 session indexes reused' in item for item in coverage)


def test_archive_missing_session_marker_falls_back_without_using_foreign_index(env):
    engine, _ = env
    manager = IndexedCodex()
    index_session(engine, manager, project_id=2)
    manager.calls.clear()
    evidence, coverage = archive_history(engine, 1, manager)
    assert manager.calls == [('a', None)]
    assert manager.metadata_calls == []
    assert all(source['session_id'] == 1 for item in evidence for source in item['sources'])
    assert any('0 session indexes reused' in item for item in coverage)


def test_index_with_unfinished_turn_cannot_be_reused_as_complete_history(env):
    engine, _ = env
    manager = IndexedCodex()
    manager.turns['a'] = [turn(1), turn(2, status='inProgress')]
    index_session(engine, manager)
    with Session(engine) as db:
        session = db.get(CodexSession, 1)
        assert session.history_indexed_at
        assert session.history_indexed_activity is None
    manager.calls.clear()
    archive_history(engine, 1, manager)
    assert manager.calls == [('a', None)]
    assert manager.metadata_calls == []


def test_index_metadata_failure_falls_back_to_existing_reader(env):
    engine, _ = env
    manager = IndexedCodex()
    index_session(engine, manager)

    def failed_metadata(*args):
        raise CodexError('Metadata unsupported')

    manager.request = failed_metadata
    manager.calls.clear()
    result, coverage = archive_history(engine, 1, manager)
    assert result
    assert manager.calls == [('a', None)]
    assert any('Index freshness could not be checked' in item for item in coverage)


def test_reduction_does_not_call_ai_for_small_singleton_tail(env):
    engine, manager = env
    evidence = [{'text': f'Fact {i}', 'sources': [
        {'id': f'note:{i}', 'label': f'Note {i}', 'text': f'Fact {i}'}]} for i in range(33)]
    reduced = summarize_evidence(engine, 1, manager, evidence)
    assert len(manager.inputs) == 2
    assert reduced[-1] == evidence[-1]


def test_profile_bound_reader_can_reuse_index(env):
    from contextlib import contextmanager

    engine, _ = env
    manager = IndexedCodex()
    with Session(engine) as db:
        db.get(CodexSession, 1).codex_account = 'profile:original'
        db.commit()
    index_session(engine, manager)
    manager.calls.clear()

    @contextmanager
    def session_reader(session):
        manager.identity = (session.id, session.project_id, session.thread_id, session.codex_account)
        try:
            yield manager
        finally:
            manager.close()

    manager.session_reader = session_reader
    result, coverage = archive_history(engine, 1, manager)
    assert result and manager.calls == []
    assert manager.metadata_calls == ['a']
    assert any('1 session indexes reused' in item for item in coverage)


def test_retry_reuses_successful_batches_before_bad_citation(env):
    engine, manager = env
    manager.turns['a'] = [turn(i, 'x' * 15000) for i in range(3)]
    generate = manager.generate_brief

    def fail_second(prompt, schema):
        manager.foreign = len(manager.inputs) == 1
        return generate(prompt, schema)

    manager.generate_brief = fail_second
    with pytest.raises(CodexError, match='foreign'):
        archive_history(engine, 1, manager)
    with Session(engine) as db:
        assert len(list(db.scalars(select(ProjectHistoryBatch)))) == 1
    first_input = manager.inputs[0]
    manager.foreign = False
    manager.generate_brief = generate
    result, coverage = archive_history(engine, 1, manager)
    assert result
    assert manager.inputs.count(first_input) == 1
    assert any('1 reused from cache' in item for item in coverage)


def test_small_long_note_edit_reuses_unchanged_chunks_and_refreshes_citations(env):
    engine, manager = env
    evidence = [{'text': 'x' * 1500, 'sources': [
        {'id': f'note:1:{i}', 'label': 'Note', 'text': 'x' * 1500, 'updated_at': 'old'}
    ]} for i in range(40)]
    summarize_evidence(engine, 1, manager, evidence)
    first_calls = len(manager.inputs)
    assert first_calls > 1
    for item in evidence:
        item['sources'][0]['updated_at'] = 'new'
    evidence[-1]['text'] += '!'
    evidence[-1]['sources'][0]['text'] += '!'
    result = summarize_evidence(engine, 1, manager, evidence)
    # The changed tail now fits verbatim after reusing earlier reductions.
    assert len(manager.inputs) == first_calls
    assert result[-1] == evidence[-1]
    assert all(source['updated_at'] == 'new' for item in result for source in item['sources'])


@pytest.mark.parametrize('change', ['label', 'text', 'classification', 'model', 'effort', 'account'])
def test_semantic_cache_invalidates_evidence_and_generation_settings(env, change):
    from backend.app.integrations.codex.archive import _summarize

    engine, manager = env
    source = {'id': 'note:1', 'label': 'Note', 'text': 'Original',
              'classification': 'reported', 'updated_at': 'old'}
    payload = {'evidence': [{'text': 'Summary input', 'source_ids': ['note:1'],
                             'source_labels': ['Note']}]}
    _summarize(engine, 1, manager, payload, [source], 'reduction')
    if change in ('label', 'text', 'classification'):
        source[change] = 'Changed'
        if change == 'label':
            payload['evidence'][0]['source_labels'] = ['Changed']
    else:
        setattr(manager, 'generation_' + change, 'changed-setting')
    _, cached = _summarize(engine, 1, manager, payload, [source], 'reduction')
    assert not cached
    assert len(manager.inputs) == 2


def test_model_visible_timestamp_still_invalidates_cache(env):
    from backend.app.integrations.codex.archive import _summarize

    engine, manager = env
    source = {'id': 'note:1', 'label': 'Note', 'text': 'Original', 'updated_at': 'old'}
    _summarize(engine, 1, manager, {'sources': [source]}, [source], 'history')
    source['updated_at'] = 'new'
    _, cached = _summarize(engine, 1, manager, {'sources': [source]}, [source], 'history')
    assert not cached
    assert len(manager.inputs) == 2


def test_semantic_cache_promotes_matching_legacy_entry_and_rebinds_metadata(env):
    import hashlib
    from backend.app.integrations.codex.ai_instructions import ai_instructions
    from backend.app.integrations.codex.archive import CACHE_VERSION, _summarize

    engine, manager = env
    source = {'id': 'note:1', 'label': 'Note', 'text': 'Original', 'updated_at': 'old'}
    payload = {'evidence': [{'text': 'Original', 'source_ids': ['note:1'], 'source_labels': ['Note']}]}
    serialized = json.dumps({'version': CACHE_VERSION, 'project_id': 1, 'stage': 'reduction',
                             'ai_instructions': ai_instructions(), 'limit': 8,
                             'payload': payload, 'sources': [source]},
                            ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
    with Session(engine) as db:
        db.add(ProjectHistoryBatch(project_id=1, fingerprint=fingerprint,
                                   evidence=[{'text': 'Cached summary', 'sources': [source]}]))
        db.commit()
    first, cached = _summarize(engine, 1, manager, payload, [source], 'reduction')
    assert cached and first[0]['text'] == 'Cached summary'
    source['updated_at'] = 'new'
    second, cached = _summarize(engine, 1, manager, payload, [source], 'reduction')
    assert cached
    assert second[0]['sources'][0]['updated_at'] == 'new'
    assert manager.inputs == []


def test_cached_citations_remain_project_and_batch_scoped(env):
    from backend.app.integrations.codex.archive import _summarize

    engine, manager = env
    source = {'id': 'note:1', 'label': 'Note', 'text': 'Original'}
    payload = {'sources': [source]}
    _summarize(engine, 1, manager, payload, [source], 'history')
    _, cached = _summarize(engine, 2, manager, payload, [source], 'history')
    assert not cached and len(manager.inputs) == 2
    with Session(engine) as db:
        row = db.scalar(select(ProjectHistoryBatch).where(ProjectHistoryBatch.project_id == 1))
        row.evidence = [{'text': 'Tampered summary', 'sources': [{'id': 'foreign'}]}]
        db.commit()
    with pytest.raises(CodexError, match='foreign'):
        _summarize(engine, 1, manager, payload, [source], 'history')
    assert len(manager.inputs) == 2


def test_archive_deduplicates_exact_fork_prefix_and_retains_every_session_reference(env):
    from copy import deepcopy

    engine, manager = env
    first = 'First accepted result: ' + 'A' * 120
    second = 'Second accepted result: ' + 'B' * 120
    manager.turns['a'] = [turn('original-1', first), turn('original-2', second),
                          turn('original-3', 'Original branch outcome')]
    manager.turns['fork'] = [turn('fork-1', first), turn('fork-2', second),
                             turn('fork-3', 'Different fork outcome'),
                             turn('fork-4', second)]
    # An identical prefix from another project must not become an alias.
    manager.turns['b'] = [turn('foreign-1', first), turn('foreign-2', second),
                          turn('foreign-3', 'FOREIGN PROJECT DATA')]
    with Session(engine) as db:
        db.add(CodexSession(id=3, project_id=1, thread_id='fork', title='Fork discussion'))
        db.commit()
    original_turns = deepcopy(manager.turns)

    evidence, _ = archive_history(engine, 1, manager)
    sent = [source for payload in manager.inputs for source in payload['sources']]
    assert [(source['session_id'], source['turn_id']) for source in sent] == [
        (1, 'original-1'), (1, 'original-2'), (1, 'original-3'),
        (3, 'fork-3'), (3, 'fork-4'),
    ]
    assert sum(source['text'].endswith(first) for source in sent) == 1
    # Equal text encountered after divergence is still a separate occurrence.
    assert sum(source['text'].endswith(second) for source in sent) == 2
    assert all('FOREIGN PROJECT DATA' not in source['text'] for source in sent)
    returned = {source['id']: source for item in evidence for source in item['sources']}
    for original, fork in (('original-1', 'fork-1'), ('original-2', 'fork-2')):
        source = returned[f'session:1:turn:{original}:part:1']
        aliases = {(alias['session_id'], alias['turn_id']) for alias in source['shared_sessions']}
        assert (3, fork) in aliases
        assert all(session_id in (1, 3) for session_id, _ in aliases)
    assert all('session:3:turn:fork-1:' not in source_id
               and 'session:3:turn:fork-2:' not in source_id for source_id in returned)
    assert manager.turns == original_turns
    assert all(thread_id != 'b' for thread_id, _ in manager.calls)
    with Session(engine) as db:
        assert db.get(CodexSession, 1).thread_id == 'a'
        assert db.get(CodexSession, 3).thread_id == 'fork'


def test_archive_keeps_short_replies_and_near_matching_prefixes_separate(env):
    engine, manager = env
    manager.turns['a'] = [turn('a-1', 'do'), turn('a-2', 'continue')]
    manager.turns['fork'] = [turn('f-1', 'do'), turn('f-2', 'continue')]
    with Session(engine) as db:
        db.add(CodexSession(id=3, project_id=1, thread_id='fork'))
        db.commit()
    evidence, _ = archive_history(engine, 1, manager)
    sent = [source for payload in manager.inputs for source in payload['sources']]
    assert len(sent) == 4
    assert all(not source.get('shared_sessions') for item in evidence for source in item['sources'])

    # Even substantial prefixes differ when an exact message changes.
    manager.inputs.clear()
    manager.turns['a'] = [turn('a-long-1', 'A' * 150), turn('a-long-2', 'B' * 150)]
    manager.turns['fork'] = [turn('f-long-1', 'A' * 150), turn('f-long-2', 'B' * 150 + '!')]
    evidence, _ = archive_history(engine, 1, manager)
    sent = [source for payload in manager.inputs for source in payload['sources']]
    assert len(sent) == 4
    assert all(not source.get('shared_sessions') for item in evidence for source in item['sources'])


def test_archive_new_fork_aliases_reuse_canonical_cache_and_failed_suffix_stays_uncached(env):
    engine, manager = env
    manager.turns['a'] = [turn('base-1', 'A' * 150), turn('base-2', 'B' * 150)]
    archive_history(engine, 1, manager)
    canonical_input = manager.inputs[0]
    with Session(engine) as db:
        saved = [(row.fingerprint, row.evidence) for row in db.scalars(select(ProjectHistoryBatch))]
        db.add(CodexSession(id=3, project_id=1, thread_id='fork'))
        db.commit()
    manager.turns['fork'] = [turn('copied-1', 'A' * 150), turn('copied-2', 'B' * 150),
                             turn('new-result', 'A unique fork result')]
    manager.fail = True
    with pytest.raises(CodexError, match='Generation failed'):
        archive_history(engine, 1, manager)
    with Session(engine) as db:
        assert [(row.fingerprint, row.evidence) for row in db.scalars(select(ProjectHistoryBatch))] == saved
    manager.fail = False
    evidence, _ = archive_history(engine, 1, manager)
    assert manager.inputs.count(canonical_input) == 1
    assert len(manager.inputs) == 2
    first = next(source for item in evidence for source in item['sources']
                 if source['id'] == 'session:1:turn:base-1:part:1')
    assert any(alias['session_id'] == 3 and alias['turn_id'] == 'copied-1'
               for alias in first['shared_sessions'])


def test_archive_fork_aliases_cover_each_fragment_of_a_long_shared_turn(env):
    engine, manager = env
    long_text = 'L' * 8500 + 'SHARED TAIL'
    manager.turns['a'] = [turn('base-long', long_text), turn('base-next', 'N' * 220)]
    manager.turns['fork'] = [turn('fork-long', long_text), turn('fork-next', 'N' * 220)]
    with Session(engine) as db:
        db.add(CodexSession(id=3, project_id=1, thread_id='fork'))
        db.commit()
    evidence, _ = archive_history(engine, 1, manager)
    sent = [source for payload in manager.inputs for source in payload['sources']]
    assert ''.join(source['text'] for source in sent).count('SHARED TAIL') == 1
    assert all(source['session_id'] == 1 for source in sent)
    shared_fragments = [source for item in evidence for source in item['sources']
                        if source['turn_id'] == 'base-long']
    assert len(shared_fragments) == 3
    assert all(any(alias['session_id'] == 3 and alias['turn_id'] == 'fork-long'
                   for alias in source['shared_sessions']) for source in shared_fragments)


def test_reduction_reuses_text_when_only_fork_links_change(env):
    from backend.app.integrations.codex.archive import _summarize
    engine, manager = env
    source = {'id': 'session:1:turn:a', 'label': 'Original', 'text': 'Original text',
              'shared_sessions': [{'session_id': 2, 'turn_id': 'b'}]}
    payload = {'evidence': [{'text': 'Fact', 'source_ids': [source['id']]}]}
    _summarize(engine, 1, manager, payload, [source], 'reduction')
    calls = len(manager.inputs)
    source['shared_sessions'].append({'session_id': 4, 'turn_id': 'c'})
    result, cached = _summarize(engine, 1, manager, payload, [source], 'reduction')
    assert cached and len(manager.inputs) == calls
    assert result[0]['sources'][0]['shared_sessions'] == source['shared_sessions']


@pytest.mark.parametrize('count', [25, 32])
def test_reduction_preserves_tail_once_full_input_fits(env, count):
    from backend.app.integrations.codex import progress

    engine, manager = env
    evidence = [{'text': f'Independent fact {i}', 'sources': [
        {'id': f'note:{i}', 'label': f'Note {i}', 'text': f'Exact original {i}'}]}
        for i in range(count)]
    progress.begin(1, 'brief')
    try:
        reduced = summarize_evidence(engine, 1, manager, evidence)
        assert len(manager.inputs) == 1
        assert reduced[8:] == evidence[16:]
        assert all(actual is original for actual, original in zip(reduced[8:], evidence[16:]))
        assert len(reduced) <= 24
        status = progress.read(1, 'brief')
        assert status['batch_total'] == status['batch_completed'] == 1
        assert status['cache_hits'] == 0
        replay = summarize_evidence(engine, 1, manager, evidence)
        assert replay == reduced
        assert len(manager.inputs) == 1
        status = progress.read(1, 'brief')
        assert status['batch_total'] == status['batch_completed'] == status['cache_hits'] == 1
        assert replay[8:] == evidence[16:]
    finally:
        progress.finish(1)


def test_reduction_cannot_skip_tail_that_still_exceeds_character_limit(env):
    engine, manager = env
    evidence = [{'text': f'Fact {i}: ' + 'x' * 1450, 'sources': [
        {'id': f'note:{i}', 'label': f'Note {i}', 'text': f'Exact original {i}'}]}
        for i in range(32)]
    reduced = summarize_evidence(engine, 1, manager, evidence)
    assert len(manager.inputs) == 2
    payload = {'evidence': [{'text': item['text'],
                            'source_ids': [s['id'] for s in item['sources']],
                            'source_labels': [s['label'] for s in item['sources']]}
                           for item in reduced]}
    assert len(reduced) <= 24
    assert len(json.dumps(payload, ensure_ascii=False)) <= BATCH_CHARS
    assert all(s in evidence[int(s['id'].split(':')[1])]['sources']
               for item in reduced for s in item['sources'])
