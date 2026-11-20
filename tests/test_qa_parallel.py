"""Real SQLite caches and threaded scheduling; AI/transport responses are mocked."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
import threading
import time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import archive, progress, qa
from backend.app.integrations.codex.protocol import CodexError, JSONFormattingError
from backend.app.integrations.codex.manager import CodexManager, _structural_output_schema
from backend.app.models import GenerationRevision, Project, ProjectHistoryBatch, ProjectQA


_marker = ContextVar('qa_parallel_marker', default=None)


@pytest.fixture
def engine(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')])
        db.commit()
    yield engine
    engine.dispose()


def points(count):
    return [{'text': str(index), 'sources': [{
        'id': f'note:{index}', 'label': f'Note {index}', 'text': f'Original {index}',
    }]} for index in range(count)]


class ParallelManager:
    generation_model = 'fixture'

    def __init__(self):
        self.lock = threading.Lock()
        self.active = self.peak = self.children = 0
        self.calls, self.contexts = [], []
        self.hook = None
        self.publish_metadata = False

    @contextmanager
    def independent_generation(self):
        with self.lock:
            self.children += 1
            child_id = self.children
        owner = self

        class Child:
            poisoned = False

            def generate_brief(self, prompt, schema):
                assert not self.poisoned, 'A failed transport was reused'
                try:
                    raw = owner.respond(prompt, schema, child_id)
                    if owner.publish_metadata:
                        payload = json.JSONDecoder().raw_decode(prompt.split('\n', 1)[1])[0]
                        self.generation_metadata = {'model': (
                            'actual-consolidation' if 'Select and rank existing project Q&A IDs' in prompt
                            else 'actual-draft-' + payload[0]['text'])}
                    return raw
                except JSONFormattingError:
                    self.poisoned = True
                    raise

        yield Child()

    def generate_brief(self, prompt, schema):
        raise AssertionError('Concurrent calls must use independent child managers')

    def respond(self, prompt, schema, child_id):
        payload = json.JSONDecoder().raw_decode(prompt.split('\n', 1)[1])[0]
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls.append((prompt, child_id))
            self.contexts.append((_marker.get(), progress.current_cancel_event()))
        try:
            if self.hook:
                value = self.hook(prompt, payload)
                if value is not None:
                    return value
            if 'Select and rank existing project Q&A IDs' in prompt:
                return json.dumps({'selected_ids': [item['id'] for item in payload][:schema['properties']['selected_ids']['maxItems']]})
            time.sleep(.04 if payload[0]['text'] == '0' else .01)
            progress.check_cancelled()
            return json.dumps({'items': [{
                'question': f"Question {payload[0]['text']}?", 'answer': 'Supported answer.',
                'source_ids': ['1'],
            }]})
        finally:
            with self.lock:
                self.active -= 1


def test_bounded_drafts_order_context_progress_and_lazy_project_scoped_cache(engine):
    manager = ParallelManager()
    batches = [[item] for item in points(7)]
    token = _marker.set('request instructions')
    progress.begin(1, 'qa')
    try:
        cancel = progress.current_cancel_event()
        with archive.archive_worker_scope(3):
            first = qa._draft_jobs(engine, 1, manager, batches)
            diagnostics = progress.read(1, 'qa')['diagnostics']
            assert archive.archive_diagnostics()['archive_generated_batches'] == 0
        assert manager.peak == 3 and manager.children == 7
        assert all(marker == 'request instructions' and event is cancel
                   for marker, event in manager.contexts)
        assert [batch[0][0]['question'] for batch in first] == [f'Question {i}?' for i in range(7)]
        assert diagnostics['qa_draft_workers'] == 3
        assert diagnostics['qa_draft_generated_batches'] == 7
        assert [row['batch'] for row in diagnostics['qa_draft_batch_timings']] == list(range(1, 8))
        with archive.archive_worker_scope(2):
            replay = qa._draft_jobs(engine, 1, manager, batches)
        assert replay == first and manager.children == 7
        status = progress.read(1, 'qa')
        assert status['batch_phase'] == 'qa_draft'
        assert status['batch_completed'] == status['batch_total'] == status['cache_hits'] == 7
        assert status['diagnostics']['qa_draft_cache_hits'] == 7
        with archive.archive_worker_scope(2):
            qa._draft_jobs(engine, 2, manager, batches)
        assert manager.children == 14
    finally:
        progress.finish(1)
        _marker.reset(token)


def test_legacy_manager_stays_sequential_with_captured_worker_setting(engine):
    class Legacy:
        calls = 0

        def generate_brief(self, prompt, schema):
            self.calls += 1
            return '{"items":[]}'

    manager = Legacy()
    progress.begin(1, 'qa')
    try:
        with archive.archive_worker_scope(4):
            assert qa._draft_jobs(engine, 1, manager, [[item] for item in points(3)]) == [[], [], []]
        assert manager.calls == 3
        assert progress.read(1, 'qa')['diagnostics']['qa_draft_workers'] == 1
    finally:
        progress.finish(1)


def test_actual_child_metadata_uses_batch_order_then_final_consolidation(engine, monkeypatch):
    manager = ParallelManager()
    manager.publish_metadata = True
    manager.generation_metadata = {'model': 'stale-parent'}
    with archive.archive_worker_scope(3):
        qa._draft_jobs(engine, 1, manager, [[item] for item in points(3)])
    # Draft zero finishes last, but batch order chooses the last batch metadata.
    assert manager.generation_metadata == {'model': 'actual-draft-2'}
    monkeypatch.setattr(qa, 'archive_history', lambda *args: (points(3), []))
    monkeypatch.setattr(qa, '_DRAFT_BATCH_ITEMS', 1)
    with archive.archive_worker_scope(3):
        saved = qa.refresh_qa(engine, 1, manager)
    # All drafts were cached; final generation still publishes actual metadata.
    assert manager.children == 4
    assert saved['generation'] == manager.generation_metadata == {'model': 'actual-consolidation'}


def test_parallel_duplicate_fingerprints_do_not_fail(engine):
    manager = ParallelManager()
    barrier = threading.Barrier(3)

    def hook(prompt, payload):
        barrier.wait(timeout=2)
        return json.dumps({'items': [{
            'question': 'Question?', 'answer': f'Validated variation {threading.get_ident()}.',
            'source_ids': ['1'],
        }]})

    manager.hook = hook
    with archive.archive_worker_scope(3):
        result = qa._draft_jobs(engine, 1, manager, [points(1)] * 3)
    assert len(result) == 3
    assert result[0] == result[1] == result[2]
    assert qa.generate_items(manager, points(1), engine=engine, project_id=1) == result[0]
    with Session(engine) as db:
        assert len(list(db.scalars(select(ProjectHistoryBatch)))) == 1


def test_refresh_reversed_order_atomic_revision_and_cache_replay(engine, monkeypatch):
    manager = ParallelManager()
    monkeypatch.setattr(qa, 'archive_history', lambda *args: (points(5), []))
    monkeypatch.setattr(qa, '_DRAFT_BATCH_ITEMS', 1)
    with archive.archive_worker_scope(3):
        saved = qa.refresh_qa(engine, 1, manager)
    assert [item['question'] for item in saved['items']] == [f'Question {i}?' for i in reversed(range(5))]
    assert manager.children == 6
    with Session(engine) as db:
        revisions = list(db.scalars(select(GenerationRevision)))
        assert len(revisions) == 1 and revisions[0].snapshot == saved
        assert db.get(ProjectQA, 2) is None
    def fail_consolidation(prompt, payload):
        if 'Select and rank existing project Q&A IDs' in prompt:
            raise CodexError('Synthetic transport failure')

    manager.hook = fail_consolidation
    with archive.archive_worker_scope(2), pytest.raises(CodexError, match='Synthetic transport failure'):
        qa.refresh_qa(engine, 1, manager, max_items=24)
    assert manager.children == 7  # Five cached drafts and one failed new ranking call.
    with Session(engine) as db:
        assert qa.read_qa(db, 1) == saved
        assert len(list(db.scalars(select(GenerationRevision)))) == 1


def test_fail_fast_keeps_completed_drafts_and_separate_history_metrics(engine):
    manager = ParallelManager()
    first_done = threading.Event()

    def hook(prompt, payload):
        index = int(payload[0]['text'])
        if index == 0:
            first_done.set()
            return '{"items":[]}'
        if index == 1:
            assert first_done.wait(2)
            # Allow the sibling to commit its successful cache.
            time.sleep(.05)
            raise CodexError('Original transport failure')
        while True:
            progress.check_cancelled()
            time.sleep(.005)

    manager.hook = hook
    progress.begin(1, 'qa')
    try:
        with archive.archive_worker_scope(2):
            archive._archive_run.get()['timings'].append({
                'phase': 'history', 'batch': 1, 'seconds': .1, 'cached': True,
            })
            with pytest.raises(CodexError, match='Original transport failure'):
                qa._draft_jobs(engine, 1, manager, [[item] for item in points(8)])
        assert manager.children <= 3
        status = progress.read(1, 'qa')
        assert status['diagnostics']['archive_cache_hits'] == 1
        assert status['diagnostics']['qa_draft_generated_batches'] == 1
        with Session(engine) as db:
            assert len(list(db.scalars(select(ProjectHistoryBatch)))) == 1
    finally:
        progress.finish(1)


@pytest.mark.parametrize('deadline', [False, True])
def test_cancellation_and_deadline_propagate_into_workers(engine, deadline):
    manager = ParallelManager()
    progress.begin(1, 'qa')
    status = progress.read(1, 'qa')

    def hook(prompt, payload):
        if deadline:
            started, budget = progress._clocks[1, 'qa']
            progress._clocks[1, 'qa'] = (started - budget - 1, budget)
        else:
            progress.cancel(1, 'qa', status['started_at'])
        progress.check_cancelled()

    manager.hook = hook
    try:
        with archive.archive_worker_scope(2), pytest.raises(
                CodexError, match='time budget' if deadline else 'cancelled'):
            qa._draft_jobs(engine, 1, manager, [[item] for item in points(6)])
        assert manager.children <= 2
        with Session(engine) as db:
            assert not list(db.scalars(select(ProjectHistoryBatch)))
    finally:
        progress.finish(1)


@pytest.mark.parametrize('stage', ['draft', 'consolidation'])
def test_formatting_retry_uses_fresh_child_and_keeps_canonical_cache(engine, stage):
    manager = ParallelManager()
    attempts = []

    def hook(prompt, payload):
        attempts.append(prompt)
        if len(attempts) == 1:
            raise JSONFormattingError('Too much formatting whitespace')
        assert 'Return compact JSON' in prompt
        return '{"items":[]}' if stage == 'draft' else '{"selected_ids":[]}'

    manager.hook = hook
    if stage == 'draft':
        assert qa.generate_items(manager, points(1), engine=engine, project_id=1) == []
        assert qa.generate_items(manager, points(1), engine=engine, project_id=1) == []
    else:
        assert qa._consolidate_group(manager, [{
            'question': 'Question?', 'answer': 'Answer.', 'source_ids': ['note:0'],
        }], 25) == []
    assert len(attempts) == manager.children == 2
    assert manager.calls[0][1] != manager.calls[1][1]


@pytest.mark.parametrize('failure', [CodexError('transport failed'), JSONFormattingError('formatting failed')])
def test_only_one_formatting_retry_and_no_generic_failure_retry(engine, failure):
    manager = ParallelManager()

    def hook(prompt, payload):
        raise failure

    manager.hook = hook
    with pytest.raises(type(failure), match=str(failure)):
        qa.generate_items(manager, points(1), engine=engine, project_id=1)
    assert manager.children == (2 if isinstance(failure, JSONFormattingError) else 1)
    with Session(engine) as db:
        assert not list(db.scalars(select(ProjectHistoryBatch)))


class AdapterManager:
    """Actual schema adapter with fresh simulated transports for each attempt."""
    def __init__(self, raw, failure=None):
        self.raw = raw
        self.failure = failure or JSONFormattingError('Formatting failed')
        self.calls = []
        self.adapters = 0
        self.children = 0

    @contextmanager
    def independent_generation(self):
        self.children += 1
        owner = self

        class Child:
            def generate_brief(self, prompt, schema):
                owner.calls.append((prompt, deepcopy(schema)))
                if len(owner.calls) == 1:
                    raise owner.failure
                return owner.raw

            def generate_extraction(self, prompt, schema):
                owner.adapters += 1
                return CodexManager.generate_extraction(self, prompt, schema)

        yield Child()


@pytest.mark.parametrize('stage', ['draft', 'consolidation'])
def test_formatting_fallback_uses_structural_adapter_without_mutating_contract(engine, stage):
    manager = AdapterManager('{"items":[]}' if stage == 'draft' else '{"selected_ids":[]}')
    canonical = deepcopy(qa._SCHEMA)
    if stage == 'draft':
        assert qa.generate_items(manager, points(1), engine=engine, project_id=1) == []
        assert qa.generate_items(manager, points(1), engine=engine, project_id=1) == []
    else:
        assert qa._consolidate_group(manager, [{
            'question': 'Question?', 'answer': 'Answer.', 'source_ids': ['note:0'],
        }], 25) == []
    assert manager.adapters == 1 and manager.children == 2 and len(manager.calls) == 2
    first_prompt, full = manager.calls[0]
    fallback_prompt, structural = manager.calls[1]
    assert structural == _structural_output_schema(full)
    assert fallback_prompt.startswith('Required extraction output contract (trusted application schema):\n')
    assert json.loads(fallback_prompt.split('\n')[1]) == full
    assert first_prompt in fallback_prompt and 'Return compact JSON' in fallback_prompt
    assert qa._SCHEMA == canonical


@pytest.mark.parametrize('raw, message', [
    ('not JSON', 'invalid JSON'),
    (json.dumps({'items': [{'question': 'Q?', 'answer': 'x' * 801, 'source_ids': ['1']}]}), 'invalid text'),
    (json.dumps({'items': [{'question': 'x' * 181, 'answer': 'A.', 'source_ids': ['1']}]}), 'invalid text'),
    (json.dumps({'items': [{'question': 'Q?', 'answer': 'A.', 'source_ids': ['1']}] * 9}), 'batch limit'),
])
def test_formatting_fallback_keeps_local_draft_bounds_and_rejects_malformed_output(engine, raw, message):
    manager = AdapterManager(raw)
    with pytest.raises(CodexError, match=message):
        qa.generate_items(manager, points(1), engine=engine, project_id=1)
    assert manager.adapters == 1 and len(manager.calls) == 3
    with Session(engine) as db:
        assert not list(db.scalars(select(ProjectHistoryBatch)))


def test_generic_transport_error_never_uses_structural_adapter(engine):
    manager = AdapterManager('{"items":[]}', failure=CodexError('Transport failed'))
    with pytest.raises(CodexError, match='Transport failed'):
        qa.generate_items(manager, points(1), engine=engine, project_id=1)
    assert manager.adapters == 0 and len(manager.calls) == 1


@pytest.mark.parametrize('kind', ['answer_length', 'question_length', 'count', 'variants', 'references'])
def test_formatting_fallback_keeps_local_consolidation_bounds(kind):
    item = {'question': 'Q?', 'answer': 'A.', 'source_ids': ['1'], 'variants': []}
    if kind == 'answer_length':
        item['answer'] = 'x' * 801
    elif kind == 'question_length':
        item['question'] = 'x' * 181
    elif kind == 'variants':
        item['variants'] = [{'answer': 'A.', 'source_ids': ['1']}] * 3
    elif kind == 'references':
        item['source_ids'] = ['1'] * 7
    manager = AdapterManager(json.dumps({'items': [item] * (26 if kind == 'count' else 1)}))
    with pytest.raises(CodexError):
        qa._consolidate_group(manager, [{
            'question': 'Question?', 'answer': 'Answer.', 'source_ids': ['note:0'],
        }], 25)
    # A completed response that fails any local validation gets one correction.
    assert manager.adapters == 1
    assert len(manager.calls) == 3


def test_parallel_consolidation_rounds_keep_order_diagnostics_and_serial_final_rank(engine, monkeypatch):
    manager = ParallelManager()
    monkeypatch.setattr(qa, '_GROUP_SIZE', 4)
    candidates = [{'question': f'Question {index}?', 'answer': 'Supported.',
                   'source_ids': [f'note:{index}']} for index in range(17)]
    final_active = []

    def hook(prompt, payload):
        if progress.read(1, 'qa')['batch_phase'] == 'qa_rank':
            final_active.append(manager.active)
            assert [item['question'] for item in payload] == ['Question 0?', 'Question 16?']
            return json.dumps({'selected_ids': [item['id'] for item in payload]})
        time.sleep(.04 if payload[0]['question'] == 'Question 0?' else .01)
        progress.check_cancelled()
        return json.dumps({'selected_ids': [payload[0]['id']]})

    manager.hook = hook
    token = _marker.set('consolidation instructions')
    progress.begin(1, 'qa')
    try:
        draft_diagnostics = {'qa_draft_generated_batches': 17, 'qa_draft_cache_hits': 3,
                             'qa_draft_batch_timings': [{'phase': 'qa_draft', 'batch': 1}]}
        progress.record_diagnostics(1, draft_diagnostics)
        with archive.archive_worker_scope(3):
            archive._archive_run.get()['timings'].append({
                'phase': 'history', 'batch': 1, 'seconds': .1, 'cached': True,
            })
            result = qa.consolidate_items(manager, candidates, 25, 1)
        assert [item['question'] for item in result] == ['Question 0?', 'Question 16?']
        assert manager.peak == 3 and final_active == [1]
        assert all(marker == 'consolidation instructions' for marker, _ in manager.contexts)
        status = progress.read(1, 'qa')
        assert status['batch_phase'] == 'qa_rank'
        assert status['batch_total'] == status['batch_completed'] == 1
        diagnostics = status['diagnostics']
        assert diagnostics['archive_cache_hits'] == 1
        assert all(diagnostics[key] == value for key, value in draft_diagnostics.items())
        assert diagnostics['qa_consolidate_generated_batches'] == 7
        timings = diagnostics['qa_consolidate_batch_timings']
        assert [row['phase_run'] for row in timings] == [1] * 5 + [2] * 2
        assert [row['batch'] for row in timings] == [1, 2, 3, 4, 5, 1, 2]
    finally:
        progress.finish(1)
        _marker.reset(token)
