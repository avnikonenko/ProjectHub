"""Bounded scheduling tests; model output is mocked, SQLite caches are real."""
from contextlib import contextmanager
from copy import copy, deepcopy
import hashlib
import json
import threading
import time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.config import MAX_EXTRACTION_WORKERS
from backend.app.models import Project, ProjectHistoryBatch, ProjectKnowledge
from backend.app.integrations.codex import knowledge, knowledge_sources, progress
from backend.app.integrations.codex.ai_instructions import ai_instructions, use_project_instructions
from backend.app.integrations.codex.protocol import CodexError


@pytest.fixture
def setup(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='Test')); db.commit()
    sources = [{'id': f'source:{i}', 'role': 'user', 'text': f'Consider independent option {i}.',
                'revision': hashlib.sha256(str(i).encode()).hexdigest(), 'order': i,
                'conversation_id': 'one', 'source_key': 'one', 'label': str(i), 'timestamp': None}
               for i in range(4)]
    windows = [{'sources': [s], 'primary_ids': [s['id']], 'context_ids': []} for s in sources]
    bundle = {'project': {'id': 1}, 'sources': sources,
              'manifest': {'fingerprint': 'same', 'entries': [{'source_key': 'one', 'availability': 'available'}]},
              'coverage': {'processed': ['one'], 'unavailable': [], 'excluded': [], 'unprocessed': [], 'limitations': []}}
    monkeypatch.setattr(knowledge_sources, 'build_windows', lambda _: deepcopy(windows))
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '2')
    yield engine, bundle, windows
    engine.dispose()


def response(payload):
    source = payload['sources'][0]
    return json.dumps({'items': [{'statement': source['text'], 'category': 'PROPOSAL',
        'design_status': 'PROPOSED', 'scope': 'project', 'rationale': 'Preserve separate options.',
        'alternatives': '', 'limitations': '', 'agreement_basis': 'none', 'revises_statement': '',
        'conflicting_statements': [], 'evidence': [{'source_id': source['id'],
            'role': source['role'], 'excerpt': source['text']}]}]})


class Manager:
    def __init__(self, callback):
        self.callback = callback
        self.identity = None

    @contextmanager
    def independent_generation(self):
        clone = copy(self)
        clone.identity = object()
        yield clone

    def generate_brief(self, prompt, schema):
        assert prompt.startswith(knowledge.PROMPT)
        return self.callback(self, json.loads(prompt[len(knowledge.PROMPT):]), schema)


@pytest.mark.parametrize('workers', [2, 4])
def test_parallel_bounds_order_context_and_exact_input_cache_reuse(setup, monkeypatch, workers):
    engine, bundle, windows = setup
    lock = threading.Lock()
    if workers == 4:
        monkeypatch.delenv('PROJECTHUB_EXTRACTION_WORKERS', raising=False)
    else:
        monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', str(workers))
    first_wave = threading.Barrier(workers)
    release_first = threading.Event()
    running = maximum = 0
    calls, completions, identities, instructions = [], [], [], []

    def generate(manager, payload, schema):
        nonlocal running, maximum
        i = int(payload['primary_ids'][0].split(':')[-1])
        with lock:
            running += 1; maximum = max(maximum, running)
            calls.append(payload); identities.append(manager.identity); instructions.append(ai_instructions())
        try:
            if i < workers:
                first_wave.wait(3)
            if i == 0:
                assert release_first.wait(3), 'Second worker did not continue independently'
            with lock: completions.append(i)
            if i == 2:
                release_first.set()
            return response(payload)
        finally:
            with lock: running -= 1

    manager = Manager(generate)
    originals = deepcopy(windows)
    with use_project_instructions('Project-only instructions.'):
        candidate = knowledge.build_candidate(engine, 1, manager, bundle)
    assert maximum == workers
    assert completions[0] != 0
    assert len(set(identities)) == 4
    assert all('Project-only instructions.' in instruction for instruction in instructions)
    assert [i['statement'] for i in candidate['items']] == [w['sources'][0]['text'] for w in windows]
    assert windows == originals
    assert sorted((p['primary_ids'], p['sources'][0]['text']) for p in calls) == sorted(
        (w['primary_ids'], w['sources'][0]['text']) for w in originals)
    assert candidate['diagnostics']['extraction_workers'] == workers
    assert len(candidate['diagnostics']['batch_timings']) == 4
    manager.callback = lambda *_: pytest.fail('Cached extraction called model')
    with use_project_instructions('Project-only instructions.'):
        replay = knowledge.build_candidate(engine, 1, manager, bundle)
    assert replay['items'] == candidate['items']
    assert replay['diagnostics']['cache_hits'] == 4
    assert replay['diagnostics']['ai_requests'] == 0


def _six_windows(bundle, windows):
    # More windows than the failure/cancellation test worker counts prove
    # undispatched work stays untouched, including the four-worker case.
    for i in (4, 5):
        source = {**windows[0]['sources'][0], 'id': f'source:{i}',
                  'text': f'Consider independent option {i}.', 'revision': f'extra:{i}', 'order': i}
        windows.append({'sources': [source], 'primary_ids': [source['id']], 'context_ids': []})
        bundle['sources'].append(source)
    return windows


@pytest.mark.parametrize('workers', [2, 4])
def test_failure_aborts_siblings_without_dispatching_more_or_changing_project_cancel(setup, monkeypatch, workers):
    engine, bundle, windows = setup
    _six_windows(bundle, windows)
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', str(workers))
    all_started = threading.Barrier(workers)
    calls, stopped, errors = [], [], []
    def generate(_, payload, schema):
        index = int(payload['primary_ids'][0].split(':')[-1]); calls.append(index)
        all_started.wait(3)
        if index == 0: raise CodexError('Original failed batch')
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                progress.check_cancelled(); time.sleep(.01)
            pytest.fail('Sibling was not aborted within the bounded wait')
        except CodexError as exc:
            errors.append(str(exc))
            raise
        finally: stopped.append(index)
    progress.begin(1, 'knowledge')
    try:
        with pytest.raises(CodexError, match='Original failed batch'):
            knowledge.build_candidate(engine, 1, Manager(generate), bundle)
        assert sorted(stopped) == list(range(1, workers))
        assert errors == ['Another extraction batch failed.'] * (workers - 1)
        assert sorted(calls) == list(range(workers))
        assert not progress.current_cancel_event().is_set()
        progress.finish(1, CodexError('Original failed batch'))
        assert progress.read(1, 'knowledge')['state'] == 'failed'
        with Session(engine) as db: assert db.get(ProjectKnowledge, 1) is None
    finally: progress.finish(1)


def test_completed_batch_cache_survives_failed_sibling(setup, monkeypatch):
    engine, bundle, windows = setup
    monkeypatch.setattr(knowledge_sources, 'build_windows', lambda _: windows[:2])
    cached = threading.Event()
    original = knowledge.extract_batch
    def tracked(*args, **kwargs):
        result = original(*args, **kwargs)
        cached.set()
        return result
    monkeypatch.setattr(knowledge, 'extract_batch', tracked)
    def generate(_, payload, schema):
        if payload['primary_ids'] == ['source:1']:
            assert cached.wait(3)
            raise CodexError('Second batch failed')
        return response(payload)
    with pytest.raises(CodexError, match='Second batch failed'):
        knowledge.build_candidate(engine, 1, Manager(generate), bundle)
    with Session(engine) as db:
        assert len(db.scalars(select(ProjectHistoryBatch)).all()) == 1
        assert db.get(ProjectKnowledge, 1) is None
    manager = Manager(lambda *_: pytest.fail('Successful extraction cache lost'))
    values, hit = original(engine, 1, manager, windows[0], knowledge.extraction_configuration(manager))
    assert hit and len(values) == 1


def test_failure_progress_counts_committed_success_before_coordinator_records_it(setup, monkeypatch):
    engine, bundle, windows = setup
    monkeypatch.setattr(knowledge_sources, 'build_windows', lambda _: windows[:2])
    cached = threading.Event()
    original = knowledge.extract_batch

    def tracked(*args, **kwargs):
        result = original(*args, **kwargs)
        cached.set()
        assert progress.current_abort_event().wait(3), 'Sibling failure was not recorded'
        return result

    monkeypatch.setattr(knowledge, 'extract_batch', tracked)

    def generate(_, payload, schema):
        if payload['primary_ids'] == ['source:1']:
            assert cached.wait(3)
            raise CodexError('Second batch failed')
        return response(payload)

    progress.begin(1, 'knowledge')
    try:
        with pytest.raises(CodexError, match='Second batch failed'):
            knowledge.build_candidate(engine, 1, Manager(generate), bundle)
        progress.finish(1, error='Second batch failed')
        state = progress.read(1, 'knowledge')
        assert state['state'] == 'failed'
        assert state['batch_total'] == 2
        assert state['batch_completed'] == 1
        assert state['cache_hits'] == 0
        with Session(engine) as db:
            assert len(db.scalars(select(ProjectHistoryBatch)).all()) == 1
            assert db.get(ProjectKnowledge, 1) is None
    finally:
        progress.finish(1)


@pytest.mark.parametrize('workers', [2, 4])
@pytest.mark.parametrize('mode', ['cancel', 'budget'])
def test_workers_inherit_cancellation_and_budget(setup, monkeypatch, mode, workers):
    engine, bundle, windows = setup
    _six_windows(bundle, windows)
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', str(workers))
    all_started = threading.Barrier(workers)
    triggered = threading.Event()
    calls, errors = [], []
    def generate(_, payload, schema):
        index = int(payload['primary_ids'][0].split(':')[-1]); calls.append(index)
        all_started.wait(3)
        if index == 0:
            if mode == 'cancel':
                progress.cancel(1, 'knowledge', progress.read(1, 'knowledge')['started_at'])
            else:
                monkeypatch.setitem(progress._clocks, (1, 'knowledge'), (time.monotonic() - 100, 30))
            triggered.set()
        assert triggered.wait(3), 'Worker did not receive generation stop condition'
        try:
            progress.check_cancelled()
            pytest.fail('Worker lost generation context')
        except CodexError as exc:
            errors.append(str(exc))
            raise
    progress.begin(1, 'knowledge')
    try:
        with pytest.raises(CodexError, match='cancelled|overall time budget'):
            knowledge.build_candidate(engine, 1, Manager(generate), bundle)
        assert sorted(calls) == list(range(workers))
        assert len(errors) == workers
        expected = 'cancelled' if mode == 'cancel' else 'overall time budget'
        assert all(expected in error for error in errors)
    finally: progress.finish(1)


@pytest.mark.parametrize('value', ['0', str(MAX_EXTRACTION_WORKERS + 1), 'wrong'])
def test_invalid_concurrency_is_explicit(setup, monkeypatch, value):
    engine, bundle, _ = setup
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', value)
    with pytest.raises(CodexError, match=f'WORKERS must be from 1 to {MAX_EXTRACTION_WORKERS}'):
        knowledge.build_candidate(engine, 1, Manager(lambda *_: pytest.fail('No generation expected')), bundle)


def test_one_worker_keeps_original_transport_and_order(setup, monkeypatch):
    engine, bundle, _ = setup
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '1')
    seen = []
    def generate(manager, payload, schema):
        assert manager.identity is None
        seen.append(payload['primary_ids'][0])
        return response(payload)
    result = knowledge.build_candidate(engine, 1, Manager(generate), bundle)
    assert seen == [f'source:{i}' for i in range(4)]
    assert result['diagnostics']['extraction_workers'] == 1


@pytest.mark.parametrize('requested', [6, MAX_EXTRACTION_WORKERS])
def test_more_than_four_workers_are_bounded_by_available_windows(setup, monkeypatch, requested):
    engine, bundle, windows = setup
    _six_windows(bundle, windows)
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', str(requested))
    barrier = threading.Barrier(6)
    lock = threading.Lock()
    active = maximum = 0
    pools = []
    original_pool = knowledge.ThreadPoolExecutor

    def pool(*args, **kwargs):
        pools.append(kwargs['max_workers'])
        return original_pool(*args, **kwargs)

    monkeypatch.setattr(knowledge, 'ThreadPoolExecutor', pool)

    def generate(manager, payload, schema):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            barrier.wait(5)
            return response(payload)
        finally:
            with lock:
                active -= 1

    result = knowledge.build_candidate(engine, 1, Manager(generate), bundle)
    assert maximum == 6
    assert pools == [6]
    assert result['diagnostics']['extraction_workers'] == 6
    assert result['diagnostics']['ai_requests'] == 6
    assert len(result['items']) == 6


@pytest.mark.parametrize('failure', [RuntimeError('private thread capacity'), OSError('private resource limit')])
def test_pool_start_resource_failure_is_explicit_and_sanitized(setup, monkeypatch, failure):
    engine, bundle, _ = setup

    def exhausted_pool(**kwargs):
        raise failure

    monkeypatch.setattr(knowledge, 'ThreadPoolExecutor', exhausted_pool)
    with pytest.raises(CodexError, match='Unable to start AI workers.*Reduce the parallel thread count') as exc:
        knowledge.build_candidate(engine, 1, Manager(lambda *_: pytest.fail('No generation expected')), bundle)
    assert 'private' not in str(exc.value)
    with Session(engine) as db:
        assert list(db.scalars(select(ProjectHistoryBatch))) == []


@pytest.mark.parametrize('failure', [RuntimeError('thread startup failed'), OSError('resource limit')])
def test_submit_resource_failure_shuts_down_pool_and_preserves_completed_cache(setup, monkeypatch, failure):
    from concurrent.futures import Future

    engine, bundle, _ = setup
    pools = []

    class ExhaustedPool:
        def __init__(self, **kwargs):
            self.submissions = 0
            self.shutdown_args = None
            pools.append(self)

        def submit(self, function, *args):
            self.submissions += 1
            if self.submissions == 2:
                raise failure
            future = Future()
            future.set_result(function(*args))
            return future

        def shutdown(self, **kwargs):
            self.shutdown_args = kwargs

    monkeypatch.setattr(knowledge, 'ThreadPoolExecutor', ExhaustedPool)
    with pytest.raises(CodexError, match='Unable to start AI workers.*Reduce the parallel thread count'):
        knowledge.build_candidate(engine, 1, Manager(lambda _, payload, schema: response(payload)), bundle)
    assert pools[0].submissions == 2
    assert pools[0].shutdown_args == {'wait': True, 'cancel_futures': True}
    with Session(engine) as db:
        assert len(list(db.scalars(select(ProjectHistoryBatch)))) == 1
