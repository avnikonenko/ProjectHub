"""Bounded presentation selection; original knowledge and evidence are never changed."""
from collections import Counter, defaultdict, deque
from hashlib import sha256

from .protocol import CodexError


def select_overview_sources(sources, measure, limit, *, companions=None):
    positions = {source['id']: index for index, source in enumerate(sources)}
    companions = companions or {}
    context_sources = {}
    for required in companions.values():
        for source in required:
            identifier = source['id']
            if (identifier in positions or 'item' in source
                    or (identifier in context_sources and context_sources[identifier] != source)):
                raise CodexError('Overview context companions have conflicting source identities.')
            context_sources[identifier] = source

    def canonical(values):
        unique = {source['id']: source for source in values}
        return sorted(unique.values(), key=lambda source: (
            0, positions[source['id']]) if source['id'] in positions else (1, source['id']))

    def cost(values):
        return measure(canonical(values))

    def required_context(unit):
        # Expand only the supplied knowledge members, never companion links.
        return {source['id']: source for member in unit
                for source in companions.get(member['id'], [])}

    fixed = [source for source in sources if 'item' not in source]
    items = {source['id']: source for source in sources if 'item' in source}
    if cost(fixed) > limit:
        raise CodexError('Overview synthesis input limit: metadata and repository evidence exceed the bound; no records were truncated.')
    parent = {identifier: identifier for identifier in items}

    def find(identifier):
        while parent[identifier] != identifier:
            parent[identifier] = parent[parent[identifier]]
            identifier = parent[identifier]
        return identifier

    missing = set()
    for identifier, source in items.items():
        item = source['item']
        related = [*item.get('conflicts_with', []),
                   *(r['target'] for r in item.get('relationships', []) if r.get('target'))]
        for target in related:
            if target in items:
                parent[find(target)] = find(identifier)
            else:
                missing.add(target)
    components = defaultdict(list)
    for identifier in sorted(items):
        components[find(identifier)].append(items[identifier])

    def priority(source):
        item = source['item']
        if item.get('design_status') == 'AGREED':
            return 0 if item.get('category') == 'REQUIREMENT' else 1
        if item.get('category') == 'REQUIREMENT':
            return 2
        if item.get('design_status') in ('UNRESOLVED', 'UNDER_EVALUATION') or item.get('category') == 'OPEN_QUESTION':
            return 3
        if item.get('design_status') == 'PROPOSED':
            return 4
        return 5

    # Weighted classes reserve most opportunities for accepted choices and
    # requirements, while retaining questions, proposals and reported context.
    # Weights count whole relationship units, not bytes or semantic importance.
    # Within each class, give every exact scope a turn before revisiting it.
    # Sparse scopes go first; a stable hash breaks ties without making spelling
    # or alphabetical position an implicit importance ranking.
    lanes = defaultdict(lambda: defaultdict(deque))
    for unit in sorted(components.values(), key=lambda unit: unit[0]['id']):
        representative = min(unit, key=lambda source: (priority(source), source['id']))
        lanes[priority(representative)][representative['item'].get('scope', '')].append(unit)
    queues = {}
    for lane, scopes in lanes.items():
        order = sorted(scopes, key=lambda scope: (
            len(scopes[scope]), sha256(scope.encode('utf-8')).digest(), scope))
        queue = deque()
        while any(scopes.values()):
            for scope in order:
                if scopes[scope]:
                    queue.append(scopes[scope].popleft())
        queues[lane] = queue
    schedule = (0, 1, 0, 2, 0, 3, 0, 1, 0, 4, 0, 2, 0, 3, 0, 1, 5)
    selected, oversized, omitted_context_units = [], [], []
    selected_context = {}
    while any(queues.values()):
        for lane in schedule:
            queue = queues.get(lane)
            if not queue:
                continue
            unit = queue.popleft()
            unit_context = required_context(unit)
            trial = [*fixed, *selected, *unit, *selected_context.values(), *unit_context.values()]
            if cost(trial) <= limit:
                selected.extend(unit)
                selected_context.update(unit_context)
            else:
                if unit_context:
                    omitted_context_units.append([source['id'] for source in unit])
                if cost([*fixed, *unit, *unit_context.values()]) > limit:
                    oversized.append([source['id'] for source in unit])
    if items and not selected:
        raise CodexError('Overview synthesis input limit: No complete knowledge item or related conflict group fits; evidence was not truncated.')
    chosen = {source['id'] for source in selected}
    result = canonical([*(source for source in sources if 'item' not in source or source['id'] in chosen),
                        *selected_context.values()])
    return result, {'policy': ('accepted_requirements_with_later_context_v3' if companions
                               else 'accepted_requirements_whole_records_v2'), 'partial': True,
                    'available_items': len(items), 'selected_items': len(chosen),
                    'available_by_status': dict(Counter(s['item'].get('design_status', 'UNKNOWN') for s in items.values())),
                    'selected_by_status': dict(Counter(s['item'].get('design_status', 'UNKNOWN') for s in selected)),
                    'available_by_category': dict(Counter(s['item'].get('category', 'UNKNOWN') for s in items.values())),
                    'selected_by_category': dict(Counter(s['item'].get('category', 'UNKNOWN') for s in selected)),
                    'selected_context_sources': len(selected_context),
                    'selected_context_ids': sorted(selected_context),
                    'omitted_context_required_units': len(omitted_context_units),
                    'selected_ids': sorted(chosen), 'omitted_ids': sorted(set(items) - chosen),
                    'omitted_items': len(items) - len(chosen),
                    'oversized_component_ids': oversized,
                    'unavailable_related_ids': sorted(missing)}
