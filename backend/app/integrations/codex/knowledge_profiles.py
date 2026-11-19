"""Per-run knowledge trade-offs, keeping evidence and sources authoritative."""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from ...generation_profiles import get_generation_profile
from .protocol import CodexError

_profile = ContextVar('projecthub_knowledge_profile', default=None)
_workers = ContextVar('projecthub_knowledge_profile_workers', default=None)


def current_knowledge_profile():
    try:
        return _profile.get() or get_generation_profile()
    except ValueError as exc:
        raise CodexError(str(exc)) from exc


@contextmanager
def use_knowledge_profile(profile, workers=None):
    token = _profile.set(profile)
    worker_token = _workers.set(workers)
    try:
        yield profile
    finally:
        _workers.reset(worker_token)
        _profile.reset(token)


def captured_knowledge_workers():
    return _workers.get()


def profile_cache_settings(profile):
    # Preserve the tested Quality cache identity, including existing installations.
    if profile.id == 'quality':
        return None
    return {'id': profile.id, 'window_chars': profile.window_chars,
            'source_budget_chars': profile.source_budget_chars,
            'selection_policy': 'whole_windows_round_robin_v1'}


def select_profile_windows(bundle, windows, profile, *, overview_source_ids=()):
    """Budget whole context-preserving windows fairly across conversations.

    The budget counts distinct source characters, including context. Repeated
    context and synthesis can consume additional tokens. Original source manifests
    are untouched: extraction coverage is distinct from evidence availability.
    """
    selected = list(range(len(windows)))
    if profile.source_budget_chars is not None:
        conversations = {}
        for index, window in enumerate(windows):
            primary = set(window['primary_ids'])
            conversation = next(source['conversation_id'] for source in window['sources']
                                if source['id'] in primary)
            conversations.setdefault(conversation, deque()).append(index)
        if overview_source_ids:
            for conversation, queue in conversations.items():
                conversations[conversation] = deque(sorted(
                    queue, key=lambda index: not overview_source_ids.intersection(windows[index]['primary_ids'])))
        order = []
        while any(conversations.values()):
            for queue in conversations.values():
                if queue:
                    order.append(queue.popleft())
        selected, included, characters = [], {}, 0
        for index in order:
            added = {source['id']: len(source['text']) for source in windows[index]['sources']}
            cost = sum(max(0, length - included.get(identifier, 0))
                       for identifier, length in added.items())
            if characters + cost <= profile.source_budget_chars:
                selected.append(index)
                for identifier, length in added.items():
                    included[identifier] = max(length, included.get(identifier, 0))
                characters += cost
        selected.sort()  # Interpretation/reconciliation order remains source order.
    chosen = [windows[index] for index in selected]
    primary = {identifier for window in chosen for identifier in window['primary_ids']}
    used = {}
    for window in chosen:
        for source in window['sources']:
            if len(source['text']) > len(used.get(source['id'], {}).get('text', '')):
                used[source['id']] = source
    available = list(dict.fromkeys(identifier for window in windows for identifier in window['primary_ids']))
    omitted = [identifier for identifier in available if identifier not in primary]
    coverage = deepcopy(bundle['coverage'])
    if omitted:
        coverage['not_extracted_by_profile'] = omitted
        coverage.setdefault('limitations', []).append(
            f'{profile.label} has partial extraction coverage: {len(primary)} of '
            f'{len(available)} primary fragments selected within '
            f'{profile.source_budget_chars:,} distinct source characters. '
            'Whole proposal/context windows are retained; omitted primary fragments '
            'must not be treated as evidence that a decision was never made.')
    selection = {'available_primary_fragments': len(available),
                 'selected_primary_fragments': len(primary),
                 'context_only_fragments': len(set(used) - primary),
                 'omitted_primary_fragments': len(omitted),
                 'selected_source_chars': sum(len(source['text']) for source in used.values()),
                 'partial': bool(omitted)}
    return chosen, coverage, selection


def configure_profile_manager(manager, profile):
    """Apply an optional default without overriding explicit model/effort choices."""
    model = getattr(manager, 'generation_model', None)
    requested = getattr(manager, 'generation_effort', None)
    notes = []
    if profile.experimental:
        notes.append('Experimental profile; review knowledge recall and source coverage.')
    if profile.default_effort and requested:
        notes.append('Your explicit reasoning effort takes precedence over the profile default.')
    elif profile.default_effort and model:
        models = manager.generation_models()
        option = next((option for option in models if option['id'] == model), None)
        if option is None:
            raise CodexError('The selected model is unavailable. Choose another model or Codex default.')
        if profile.default_effort in option['efforts']:
            manager = manager.for_generation(model, profile.default_effort)
        else:
            notes.append('This model does not advertise the profile reasoning default; its model default is retained.')
    elif profile.default_effort:
        notes.append('Codex default model keeps its default reasoning. Select a model to use the profile reasoning default.')
    if profile.source_budget_chars is not None:
        notes.append('The source budget limits distinct text, not tokens; repeated context and synthesis add usage.')
    return manager, {'id': profile.id, 'label': profile.label,
                     'window_chars': profile.window_chars,
                     'source_budget_chars': profile.source_budget_chars,
                     'requested_effort': requested,
                     'effective_effort': getattr(manager, 'generation_effort', None),
                     'experimental': profile.experimental, 'notes': notes}
