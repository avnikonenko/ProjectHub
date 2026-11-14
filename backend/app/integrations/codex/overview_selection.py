"""Bounded presentation selection; original knowledge and evidence are never changed."""
from collections import defaultdict, deque

from .protocol import CodexError


def select_overview_sources(sources, measure, limit):
    positions = {source['id']: index for index, source in enumerate(sources)}
    def cost(values):
        return measure(sorted(values, key=lambda source: positions[source['id']]))
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
            return 0
        if item.get('category') == 'REQUIREMENT':
            return 1
        if item.get('design_status') in ('UNRESOLVED', 'UNDER_EVALUATION') or item.get('category') == 'OPEN_QUESTION':
            return 2
        return 3

    # Round-robin status/category lanes; each lane rotates exact scopes. This is
    # a stable display policy, not a chronology or a semantic importance score.
    lanes = defaultdict(lambda: defaultdict(deque))
    for unit in sorted(components.values(), key=lambda unit: unit[0]['id']):
        representative = min(unit, key=lambda source: (priority(source), source['id']))
        item = representative['item']
        lanes[(priority(representative), item.get('design_status', ''), item.get('category', ''))][item.get('scope', '')].append(unit)
    queues = []
    for lane in sorted(lanes):
        scopes = lanes[lane]
        queue = deque()
        while any(scopes.values()):
            for scope in sorted(scopes):
                if scopes[scope]:
                    queue.append(scopes[scope].popleft())
        queues.append(queue)
    selected, oversized = [], []
    while any(queues):
        for queue in queues:
            if not queue:
                continue
            unit = queue.popleft()
            trial = [*fixed, *selected, *unit]
            if cost(trial) <= limit:
                selected.extend(unit)
            else:
                if cost([*fixed, *unit]) > limit:
                    oversized.append([source['id'] for source in unit])
    if items and not selected:
        raise CodexError('Overview synthesis input limit: No complete knowledge item or related conflict group fits; evidence was not truncated.')
    chosen = {source['id'] for source in selected}
    result = [source for source in sources if 'item' not in source or source['id'] in chosen]
    return result, {'policy': 'balanced_whole_records_v1', 'partial': True,
                    'available_items': len(items), 'selected_items': len(chosen),
                    'selected_ids': sorted(chosen), 'omitted_ids': sorted(set(items) - chosen),
                    'omitted_items': len(items) - len(chosen),
                    'oversized_component_ids': oversized,
                    'unavailable_related_ids': sorted(missing)}
