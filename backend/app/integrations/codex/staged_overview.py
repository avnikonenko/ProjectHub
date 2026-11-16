"""Bounded staged Overview synthesis, retaining original evidence and raw Knowledge.

Intermediate points are interpretations, not new evidence. Every cited point is
expanded back to exact original quotes and stable original source records before
it can be supplied to a later stage. Stages never publish project state.
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from copy import deepcopy
import hashlib
import json
import threading
import time

from sqlalchemy.orm import Session

from ...models import ProjectHistoryBatch
from . import progress
from .knowledge import extraction_configuration, _extraction_workers
from .overview_context import build_overview_companions
from .protocol import CodexError

MAX_LEVELS = 8
CACHE_VERSION = 1


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def _requested_provenance(manager):
    """Keep requested defaults distinct from metadata learned in a model reply.

    Codex's for_generation interface represents requests through generation_*
    attributes. Missing or None means the provider default, even after a reply
    has populated generation_metadata. Lightweight test adapters without that
    request interface can declare their fixed identity through metadata instead.
    """
    metadata = getattr(manager, 'generation_metadata', {})
    request_interface = callable(getattr(manager, 'for_generation', None))
    provenance = {}
    for name in ('account', 'model', 'effort'):
        attribute = 'generation_' + name
        if hasattr(manager, attribute) or request_interface or (name == 'account' and hasattr(manager, 'account_key')):
            value = getattr(manager, attribute, None)
            if name == 'account' and value is None:
                value = getattr(manager, 'account_key', None)
        else:
            value = metadata.get(name) if isinstance(metadata, dict) else None
        provenance[name] = str(value or 'default')
    if request_interface:
        from pathlib import Path
        from .accounts import launch_home
        home = getattr(manager, 'generation_account', None) or getattr(manager, 'account_home', None)
        # The launch account can change between restarts without an account_key.
        # Bind only its declared directory identity; never inspect credentials.
        provenance['account_home'] = str(Path(home).expanduser().resolve() if home else launch_home())
    return provenance


def _unique(records):
    unique = {}
    for record in records:
        identifier = record['id']
        if identifier in unique and unique[identifier] != record:
            raise CodexError('Overview sources contain conflicting records for the same identity; '
                             'saved results are preserved.')
        unique[identifier] = record
    return list(unique.values())


def _partition(units, measure, maximum):
    """Balanced binary splits avoid repeatedly encoding every growing prefix."""
    batches = []

    def split(current):
        sources = _unique(record for unit in current for record in unit)
        size = measure(sources)
        if size <= maximum:
            batches.append(sources)
        elif len(current) == 1:
            raise CodexError('One Overview item and its original evidence exceed the synthesis input limit; '
                             'saved results are preserved. Shortening unrelated records would not fix it.')
        else:
            middle = len(current) // 2
            split(current[:middle])
            split(current[middle:])

    split(units)
    return batches


def _support_catalog(sources, groups):
    """Use known literal quotes, never entire fragments in place of short support."""
    catalog = {}
    for source in sources:
        originals = {original['id']: original for original in groups[source['id']]}
        if 'item' in source:
            evidence = source['item'].get('evidence', [])
        elif source.get('kind') == 'original_conversation_context':
            evidence = [{'source_id': source['source_id'], 'role': source.get('role'), 'excerpt': quote,
                         'conversation_id': source.get('conversation_id'), 'order': source.get('order')}
                        for quote in source.get('excerpts', [])]
        else:
            evidence = [{'source_id': original['id'], 'role': original.get('role'),
                         'excerpt': original.get('text'), 'conversation_id': original.get('conversation_id'),
                         'order': original.get('order')} for original in originals.values()]
        for ref in evidence:
            original = originals.get(ref.get('source_id'))
            quote = ref.get('excerpt')
            literal = original.get('excerpts') if original is not None else None
            if not isinstance(literal, list) or not literal or not all(isinstance(value, str) for value in literal):
                literal = [original.get('text')] if original is not None else []
            if (original is None or not isinstance(quote, str) or not quote.strip()
                    or not any(isinstance(value, str) and quote in value for value in literal)):
                raise CodexError('An Overview batch has missing or changed original evidence; saved results are preserved.')
            view = {key: deepcopy(value) for key, value in ref.items()
                    if key in ('source_id', 'role', 'excerpt', 'conversation_id', 'order', 'revision', 'message_order')}
            bucket = catalog.setdefault(original['id'], [])
            if view not in bucket:
                bucket.append(view)
    return catalog


def _summary_sources(result, sources, groups, level, batch, entry_references=None):
    sections, graph, cited = result
    originals = {source['id']: source for source in cited}
    source_views = {source['id']: source for source in sources}
    shared_quotes = _support_catalog(sources, groups) if entry_references is None else None
    summaries, summary_groups = [], {}
    node_labels = {point['id']: point['label'] for point in graph.get('nodes', [])}
    nodes = {point['id']: point for point in graph.get('nodes', [])}
    node_support = {point['id']: [*point.get('supporting_source_ids', []), *point['source_ids']]
                    for point in graph.get('nodes', [])}
    points = [(name, point) for name, entries in sections.items() for point in entries]
    points += [('project_map.' + name, point) for name, entries in graph.items() for point in entries]
    for index, (section, point) in enumerate(points):
        endpoint_support = [ref for key in ('from', 'to')
                            for ref in node_support.get(point.get(key), [])] if section == 'project_map.edges' else []
        refs = list(dict.fromkeys([*point.get('supporting_source_ids', []), *point['source_ids'], *endpoint_support]))
        if entry_references is not None:
            aliases = [*entry_references[id(point)]]
            if section == 'project_map.edges':
                aliases.extend(alias for key in ('from', 'to')
                               for alias in entry_references[id(nodes[point[key]])])
            quotes = _support_catalog([source_views[alias] for alias in dict.fromkeys(aliases)], groups)
        else:
            quotes = shared_quotes
        if not refs or any(ref not in originals or ref not in quotes for ref in refs):
            raise CodexError('An Overview stage lost its original evidence references; saved results are preserved.')
        identifier = f'overview-stage:{level}:{batch}:{index}'
        evidence = []
        for ref in refs:
            for quote in quotes[ref]:
                if quote not in evidence:
                    evidence.append(deepcopy(quote))
        statement = point.get('text', point.get('label'))
        if section == 'project_map.edges':
            statement = f"{node_labels[point['from']]} → {node_labels[point['to']]}: {statement}"
        summaries.append({'id': identifier, 'kind': 'staged_interpretation',
                          'classification': point.get('classification', 'inferred'),
                          'item': {'statement': statement, 'section': section,
                                   'source_state': 'valid', 'agreement_basis': 'none',
                                   'staged_support': True, 'evidence': evidence}})
        summary_groups[identifier] = [deepcopy(originals[ref]) for ref in refs]
    return summaries, summary_groups


class _StageManager:
    def __init__(self, manager, engine, project_id, identity, diagnostics):
        self.manager, self.engine, self.project_id = manager, engine, project_id
        self.identity, self.diagnostics = identity, diagnostics
        self.fingerprint = self.raw = None
        self.cached = False

    def __getattr__(self, name):
        return getattr(self.manager, name)

    def generate_brief(self, prompt, schema):
        progress.check_cancelled()
        self.fingerprint = _digest({'overview_stage_version': CACHE_VERSION, 'project_id': self.project_id,
                                    'identity': self.identity, 'prompt': prompt, 'schema': schema,
                                    'configuration': extraction_configuration(self.manager)})
        lookup_started = time.monotonic()
        if self.engine is not None:
            with Session(self.engine) as db:
                cached = db.get(ProjectHistoryBatch, (self.project_id, self.fingerprint))
                if cached is not None:
                    envelope = cached.evidence
                    if (not isinstance(envelope, list) or len(envelope) != 1
                            or not isinstance(envelope[0], dict)
                            or not isinstance(envelope[0].get('stage_response'), str)):
                        raise CodexError('Saved Overview batch cache is invalid; saved results are preserved.')
                    self.raw, self.cached = envelope[0]['stage_response'], True
                    self.diagnostics['cache_lookup_seconds'] += time.monotonic() - lookup_started
                    return self.raw
        self.diagnostics['cache_lookup_seconds'] += time.monotonic() - lookup_started
        self.diagnostics['synthesis_requests'] += 1
        model_started = time.monotonic()
        try:
            self.raw = self.manager.generate_brief(prompt, schema)
            return self.raw
        finally:
            self.diagnostics['model_seconds'] += time.monotonic() - model_started

    def save_validated(self):
        progress.check_cancelled()
        if self.cached:
            self.diagnostics['synthesis_cache_hits'] += 1
        elif self.engine is not None:
            with Session(self.engine) as db:
                if db.get(ProjectHistoryBatch, (self.project_id, self.fingerprint)) is None:
                    db.add(ProjectHistoryBatch(project_id=self.project_id, fingerprint=self.fingerprint,
                                               evidence=[{'stage_response': self.raw}]))
                    db.commit()


def staged_synthesis(manager, candidate, bundle, repository, sources, groups, *,
                     engine=None, project_id=None, diagnostics=None, full_input_chars):
    from .knowledge_brief import MAX_SYNTHESIS_CHARS, _encode_synthesis, _synthesize

    diagnostics = {} if diagnostics is None else diagnostics
    eligible = {source['id'] for source in sources if 'item' in source}
    processed = set()
    available = len(eligible)
    diagnostics.update(partial=False, available_items=available, selected_items=0, omitted_items=0,
                       staged_synthesis=True, synthesis_requests=0, synthesis_cache_hits=0,
                       synthesis_batches_total=1, synthesis_batches_completed=0,
                       synthesis_levels=0, full_input_chars=full_input_chars, input_chars=0)
    project_id = bundle.get('project', {}).get('id') if project_id is None else project_id
    if engine is not None and (type(project_id) is not int or project_id < 1):
        raise CodexError('Overview stage caching requires the selected project identity.')
    if engine is not None and candidate.get('project_id', project_id) != project_id:
        raise CodexError('Overview sources do not belong to the selected project.')
    # Reply metadata is observational and may be filled only after a live call;
    # it must never change the identity of an unchanged requested configuration.
    provenance = _requested_provenance(manager)
    companions, context_groups = build_overview_companions(candidate, sources)
    groups = {**groups, **context_groups}
    units = [[source, *companions.get(source['id'], [])] for source in sources]
    original_records, original_fingerprints = {}, {}

    def identity(records, original_groups):
        bindings = {}
        for alias, originals in original_groups.items():
            bindings[alias] = []
            for original in originals:
                identifier = original['id']
                if identifier in original_records and original_records[identifier] != original:
                    raise CodexError('Overview original sources contain conflicting identities; saved results are preserved.')
                if identifier not in original_records:
                    original_records[identifier] = original
                    original_fingerprints[identifier] = _digest(original)
                bindings[alias].append(identifier)
        used = {identifier for refs in bindings.values() for identifier in refs}
        return {'sources': records, 'source_groups': bindings,
                'original_sources': {identifier: original_fingerprints[identifier] for identifier in sorted(used)},
                'provenance': provenance}

    def measure(records):
        progress.check_cancelled()
        return len(_encode_synthesis(records, candidate, bundle, repository)[2])

    completion_lock = threading.Lock()
    diagnostics.update(synthesis_workers=1, synthesis_batch_timings=[])

    def prepare(records, original_groups, level, batch):
        # Prepare shared source identities on the coordinator, before dispatch.
        progress.check_cancelled()
        size = measure(records)
        if size > MAX_SYNTHESIS_CHARS:
            raise CodexError('An Overview stage exceeds its synthesis input limit; saved results are preserved.')
        diagnostics['input_chars'] = max(diagnostics['input_chars'], size)
        diagnostics['synthesis_levels'] = max(diagnostics['synthesis_levels'], level + 1)
        if any(source['id'] not in original_groups for source in records):
            raise CodexError('An Overview batch has missing original source groups; saved results are preserved.')
        stage_groups = {source['id']: original_groups[source['id']] for source in records}
        _support_catalog(records, stage_groups)
        return records, stage_groups, identity(records, stage_groups), size, level, batch

    def run(job, selected):
        records, stage_groups, stage_identity, size, level, batch = job
        progress.check_cancelled()
        local = dict(synthesis_requests=0, synthesis_cache_hits=0,
                     cache_lookup_seconds=0.0, model_seconds=0.0)
        proxy = _StageManager(selected, engine, project_id, stage_identity, local)
        entry_references = {}
        started = time.monotonic()
        success = False
        try:
            result = _synthesize(proxy, candidate, bundle, repository, _sources=records,
                                 _groups=stage_groups, _stage=True, _stage_references=entry_references)
            proxy.save_validated()
            success = True
            return result, entry_references, deepcopy(getattr(selected, 'generation_metadata', {})), proxy.cached
        finally:
            elapsed = time.monotonic() - started
            with completion_lock:
                diagnostics['synthesis_requests'] += local['synthesis_requests']
                diagnostics['synthesis_cache_hits'] += local['synthesis_cache_hits']
                if success:
                    diagnostics['synthesis_batches_completed'] += 1
                    if level == 0:
                        processed.update(source['id'] for source in records if source['id'] in eligible)
                        diagnostics['selected_items'] = len(processed)
                diagnostics['synthesis_batch_timings'].append({
                    'level': level, 'batch': batch + 1, 'input_chars': size,
                    'seconds': round(elapsed, 3), 'model_seconds': round(local['model_seconds'], 3),
                    'cache_lookup_seconds': round(local['cache_lookup_seconds'], 3),
                    'local_seconds': round(max(0, elapsed - local['model_seconds'] - local['cache_lookup_seconds']), 3),
                    'cached': proxy.cached, 'completed': success})
                diagnostics['synthesis_batch_timings'].sort(key=lambda row: (row['level'], row['batch']))
                # Cancellation can reject progress updates after a valid cache
                # was committed; never replace its originating exception.
                if success:
                    progress.update(project_id, batch_completed=diagnostics['synthesis_batches_completed'],
                                    cache_hits=diagnostics['synthesis_cache_hits'])

    def run_level(batches, original_groups, level):
        workers = min(_extraction_workers(manager), max(1, len(batches)))
        diagnostics['synthesis_workers'] = max(diagnostics['synthesis_workers'], workers)
        progress.update(project_id, stage='Combining overview batches' if level else 'Summarizing overview batches',
                        batch_phase='overview synthesis', batch_total=diagnostics['synthesis_batches_total'],
                        batch_completed=diagnostics['synthesis_batches_completed'],
                        cache_hits=diagnostics['synthesis_cache_hits'])
        if workers == 1:
            return [run(prepare(records, original_groups, level, index), manager)
                    for index, records in enumerate(batches)]
        jobs = [prepare(records, original_groups, level, index) for index, records in enumerate(batches)]
        abort = threading.Event()
        failure, failure_lock = [], threading.Lock()
        results = [None] * len(jobs)

        def execute(job):
            try:
                with progress.abort_on(abort):
                    progress.check_cancelled()
                    with manager.independent_generation() as selected:
                        return run(job, selected)
            except BaseException as exc:
                with failure_lock:
                    if not failure:
                        failure.append(exc)
                    abort.set()
                raise

        def check_failure():
            with failure_lock:
                if failure:
                    raise failure[0]

        try:
            pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='projecthub-overview')
        except (RuntimeError, OSError) as exc:
            raise CodexError('Unable to start AI workers. Reduce the parallel thread count and retry.') from exc
        pending, next_index = {}, 0
        try:
            while next_index < len(jobs) or pending:
                progress.check_cancelled()
                check_failure()
                while next_index < len(jobs) and len(pending) < workers:
                    check_failure()
                    try:
                        future = pool.submit(copy_context().run, execute, jobs[next_index])
                    except (RuntimeError, OSError) as exc:
                        raise CodexError('Unable to start AI workers. Reduce the parallel thread count and retry.') from exc
                    pending[future] = next_index
                    next_index += 1
                ready, _ = wait(pending, timeout=.05, return_when=FIRST_COMPLETED)
                check_failure()
                for future in ready:
                    if future.exception() is not None:
                        future.result()
                for future in sorted(ready, key=lambda value: pending[value]):
                    results[pending.pop(future)] = future.result()
        except BaseException:
            abort.set()
            for future in pending:
                future.cancel()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        # Provider metadata is selected by input order, never completion order.
        for _, _, metadata, cached in results:
            if metadata and not cached:
                manager.generation_metadata = metadata
        return results

    previous_size = measure(_unique(record for unit in units for record in unit))
    for level in range(MAX_LEVELS):
        batches = _partition(units, measure, MAX_SYNTHESIS_CHARS)
        diagnostics['synthesis_batches_total'] += len(batches)
        summaries, next_groups = [], {}
        results = run_level(batches, groups, level)
        for batch_index, (records, (result, entry_references, _, _)) in enumerate(zip(batches, results)):
            records, resolved = _summary_sources(result, records, groups, level, batch_index, entry_references)
            summaries.extend(records)
            next_groups.update(resolved)
        next_size = measure(summaries)
        if next_size <= MAX_SYNTHESIS_CHARS:
            progress.update(project_id, stage='Combining overview batches')
            return run(prepare(summaries, next_groups, level + 1, 0), manager)[0]
        if next_size >= previous_size:
            raise CodexError('Overview stages did not reduce the input enough to continue safely; '
                             'completed stage caches and saved results are preserved.')
        previous_size = next_size
        groups, units = next_groups, [[record] for record in summaries]
    raise CodexError('Overview synthesis reached its safe stage limit; '
                     'completed stage caches and saved results are preserved.')
