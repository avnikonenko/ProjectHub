"""Literal later-message companions for bounded Overview evidence selection.

Message adjacency supplies context, never proof of resolution, implementation,
acceptance or supersession. No persisted Knowledge records are changed.
"""
from collections import defaultdict
from copy import deepcopy


def _position(source):
    conversation, turn = source.get('conversation_id'), source.get('turn_id')
    message, order = source.get('message_order'), source.get('order')
    if (not isinstance(conversation, str) or not conversation
            or not isinstance(turn, str) or not turn
            or type(message) is not int or message < 1
            or type(order) is not int or order < 0):
        return None
    return (conversation, turn), message


def _excerpts(source):
    excerpts = source.get('excerpts')
    if (not isinstance(excerpts, list) or not excerpts
            or any(not isinstance(value, str) or not value.strip() for value in excerpts)):
        return None
    return excerpts


def build_overview_companions(candidate, sources):
    """Map active Knowledge views to exact saved excerpts from later replies.

    Only explicit original metadata establishes a same-turn message position.
    Source IDs are opaque. Every available saved fragment of the last assistant
    message accompanies an earlier cited message, except fragments already cited
    by that item. Unknown ordering or incomplete excerpt records fail closed.
    """
    originals = {source['id']: source for source in candidate.get('sources', [])}
    candidate_items = {'knowledge:' + item['id'].removeprefix('knowledge:'): item
                       for item in candidate.get('items', [])}
    active_refs = set()
    active_views = set()
    for source in sources:
        item = source.get('item')
        if (not item or item.get('source_state') != 'valid' or item.get('superseded_by')
                or item.get('reconciliation_state') == 'unresolved'):
            continue
        saved_item = candidate_items.get(source['id'], item)
        if (saved_item.get('source_state') != 'valid' or saved_item.get('superseded_by')
                or saved_item.get('reconciliation_state') == 'unresolved'):
            continue
        active_views.add(source['id'])
        for ref in saved_item.get('evidence', []):
            original = originals.get(ref.get('source_id'))
            if original is not None and ('revision' not in ref or ref['revision'] == original.get('revision')):
                active_refs.add(original['id'])
    turns = defaultdict(list)
    unreliable_turns = set()
    for original in originals.values():
        if original.get('role') != 'assistant':
            continue
        position = _position(original)
        if position is None:
            conversation, turn = original.get('conversation_id'), original.get('turn_id')
            if isinstance(conversation, str) and isinstance(turn, str):
                unreliable_turns.add((conversation, turn))
            continue
        turns[position[0]].append(original)

    latest = {}
    for turn, records in turns.items():
        if turn in unreliable_turns:
            continue
        last_order = max(record['message_order'] for record in records)
        fragments = [record for record in records if record['message_order'] == last_order]
        if all(fragment['id'] in active_refs and _excerpts(fragment) is not None for fragment in fragments):
            latest[turn] = (last_order, sorted(fragments, key=lambda record: (record['order'], record['id'])))

    companions, groups = {}, {}
    for source in sources:
        if source['id'] not in active_views:
            continue
        cited = {ref.get('source_id') for ref in source['item'].get('evidence', [])}
        qualifying = set()
        for identifier in cited:
            original = originals.get(identifier)
            position = _position(original) if original is not None else None
            if position and position[0] in latest and position[1] < latest[position[0]][0]:
                qualifying.add(position[0])
        records = []
        for turn in sorted(qualifying):
            for original in latest[turn][1]:
                if original['id'] in cited:
                    continue
                identifier = 'overview-context:' + original['id']
                records.append({
                    'id': identifier, 'kind': 'original_conversation_context',
                    'source_id': original['id'], 'turn_id': original['turn_id'],
                    'role': 'assistant', 'conversation_id': original['conversation_id'],
                    'order': original['order'], 'message_order': original['message_order'],
                    'excerpts': deepcopy(original['excerpts']),
                })
                groups[identifier] = [{**deepcopy(original), 'classification': 'reported'}]
        if records:
            companions[source['id']] = records
    return companions, groups
