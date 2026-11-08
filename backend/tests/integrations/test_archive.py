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
