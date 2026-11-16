"""Real threads/SQLite; model replies mocked. No live Codex calls."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
import threading

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, ProjectHistoryBatch
from backend.app.integrations.codex import knowledge_brief, progress, staged_overview
from backend.app.integrations.codex.brief import SECTION_NAMES
from backend.app.integrations.codex.protocol import CodexError

marker = ContextVar('overview_test_marker', default=None)


@pytest.fixture
def state(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add(Project(id=1, name='Parallel fixture')); db.commit()
    originals = [{'id': f'original:{i}', 'role': 'user', 'text': f'Exact evidence {i}.'} for i in range(8)]
    candidate = {'project_id': 1, 'coverage': {}, 'sources': originals,
                 'items': [{'id': f'item:{i}', 'statement': f'Component {i}: ' + 'x' * 1500,
                            'source_state': 'valid', 'evidence': [{'source_id': source['id'], 'role': 'user',
                                                                'excerpt': source['text']}]}
                           for i, source in enumerate(originals)]}
    monkeypatch.setattr(knowledge_brief, 'MAX_SYNTHESIS_CHARS', 5000)
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '2')
    before = deepcopy(candidate)
    token = marker.set('request-context')
    yield engine, candidate
    assert candidate == before
    marker.reset(token)
    engine.dispose()


class Model:
    generation_account = 'fixture'
    generation_model = 'fixture-model'
    generation_effort = 'low'
    generation_metadata = {}

    def __init__(self, *, failure=None):
        self.barrier = threading.Barrier(2)
        self.lock = threading.Lock()
        self.cached = threading.Event()
        self.calls = self.active = self.maximum = self.root_calls = 0
        self.failure = failure

    @contextmanager
    def independent_generation(self):
        root = self
        class Worker:
            generation_account, generation_model, generation_effort = 'fixture', 'fixture-model', 'low'
            generation_metadata = {}
            def generate_brief(self, prompt, schema):
                return root.reply(prompt, schema, worker=True)
        yield Worker()

    def generate_brief(self, prompt, schema):
        return self.reply(prompt, schema, worker=False)

    def reply(self, prompt, schema, worker):
        assert marker.get() == 'request-context'
        with self.lock:
            number = self.calls
            self.calls += 1
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            if not worker:
                self.root_calls += 1
                assert self.active == 1, 'Dependent combine overlapped a sibling.'
        try:
            if number < 2:
                self.barrier.wait(timeout=4)
                if self.failure and number == 1:
                    assert self.cached.wait(timeout=4)
                    if self.failure == 'cancel':
                        status = progress.read(1, 'knowledge')
                        progress.cancel(1, 'knowledge', status['started_at'])
                        raise progress.GenerationCancelled()
                    raise CodexError('Originating sibling failure')
            allowed = schema['properties']['sections']['properties']['overview']['items']['properties']['source_ids']['items']['enum']
            sections = {name: [] for name in SECTION_NAMES}
            for name in ('overview', 'current_status', 'architecture'):
                sections[name] = [{'text': 'A supported point.', 'classification': 'reported', 'source_ids': [allowed[0]]}]
            return json.dumps({'sections': sections, 'project_map': {'nodes': [], 'edges': []}})
        finally:
            with self.lock:
                self.active -= 1


def generate(engine, candidate, model, diagnostics):
    return knowledge_brief._synthesize(model, candidate, {'project': {'id': 1, 'name': 'Parallel fixture'}}, {},
                                      engine=engine, project_id=1, selection_diagnostics=diagnostics)


def test_independent_summaries_overlap_with_bounded_workers_and_cache_replay(state, monkeypatch):
    engine, candidate = state
    model, diagnostics = Model(), {}
    first = generate(engine, candidate, model, diagnostics)
    assert model.maximum == diagnostics['synthesis_workers'] == 2
    assert model.root_calls >= 1
    assert diagnostics['selected_items'] == diagnostics['available_items'] == 8
    timings = diagnostics['synthesis_batch_timings']
    assert timings and all(row['completed'] and row['input_chars'] > 0 for row in timings)
    assert all(row['model_seconds'] >= 0 and row['local_seconds'] >= 0 for row in timings)
    calls = model.calls
    replay = {}
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '1')
    second = generate(engine, candidate, model, replay)
    assert first == second and model.calls == calls
    assert replay['synthesis_requests'] == 0
    assert replay['synthesis_cache_hits'] == len(timings)


@pytest.mark.parametrize('failure', ['error', 'cancel'])
def test_failure_stops_dispatch_and_combination_but_preserves_valid_cache(state, monkeypatch, failure):
    engine, candidate = state
    model, diagnostics = Model(failure=failure), {}
    original_save = staged_overview._StageManager.save_validated
    def save(proxy):
        original_save(proxy)
        model.cached.set()
        # Hold this slot until the failing sibling has set the shared abort.
        assert progress.current_abort_event().wait(timeout=4)
    monkeypatch.setattr(staged_overview._StageManager, 'save_validated', save)
    progress.begin(1, 'knowledge')
    try:
        with pytest.raises(CodexError, match='Originating sibling failure|cancelled'):
            generate(engine, candidate, model, diagnostics)
    finally:
        progress.finish(1)
    assert model.calls == 2 and model.root_calls == 0
    assert diagnostics['synthesis_batches_completed'] == 1
    with Session(engine) as db:
        assert db.scalars(select(ProjectHistoryBatch)).all()


def test_worker_metadata_uses_input_order_not_completion_order(state, monkeypatch):
    engine, candidate = state
    # Limit to a known two-leaf level plus one dependent combine.
    originals = candidate['sources'][:2]
    sources = [
        {'id': f'knowledge:{i}', 'item': {'statement': f'Leaf {i}', 'evidence': [
            {'source_id': source['id'], 'role': 'user', 'excerpt': source['text']}]}}
        for i, source in enumerate(originals)]
    groups = {source['id']: [originals[i]] for i, source in enumerate(sources)}
    monkeypatch.setattr(staged_overview, '_partition', lambda units, measure, maximum: [[sources[0]], [sources[1]]])
    monkeypatch.setattr(staged_overview, 'build_overview_companions', lambda *args: ({}, {}))
    model = Model()
    second_finished = threading.Event()
    completions = []
    @contextmanager
    def independent():
        class Worker:
            generation_account, generation_model, generation_effort = 'fixture', 'fixture-model', 'low'
            generation_metadata = {}
            def generate_brief(self, prompt, schema):
                label = json.loads(prompt.rsplit('\n', 1)[1])['sources'][0]['item']['statement']
                result = model.reply(prompt, schema, worker=True)
                if label == 'Leaf 0':
                    assert second_finished.wait(timeout=4)
                self.generation_metadata = {'model': label}
                completions.append(label)
                if label == 'Leaf 1':
                    second_finished.set()
                return result
        yield Worker()
    model.independent_generation = independent
    original_generate = model.generate_brief
    def combine(prompt, schema):
        assert model.generation_metadata == {'model': 'Leaf 1'}
        return original_generate(prompt, schema)
    model.generate_brief = combine
    staged_overview.staged_synthesis(model, candidate, {'project': {'id': 1}}, {}, sources, groups,
                                    engine=engine, project_id=1, full_input_chars=10000)
    assert completions == ['Leaf 1', 'Leaf 0']
