"""Bounded published Knowledge context, with separate historical source quotations.

This adapter never extracts knowledge or publishes data. SQL projections avoid
deserializing a project's complete Knowledge snapshot or conversation bodies.
"""
import hashlib
import json
import re

from sqlalchemy import text

from ...services import require_project
from .knowledge_sources import CONVERSATION_EXTENT_SQL, CONVERSATION_NOTE_SQL


BUDGET_CHARS = 12_000
MAX_CANDIDATES = 96
_MAX_ITEM_CHARS = 20_000
_STATUSES = ('AGREED', 'UNDER_EVALUATION', 'UNRESOLVED', 'PROPOSED', 'REJECTED', 'UNKNOWN')
_CATEGORIES = ('DECISION', 'REQUIREMENT', 'PROPOSAL', 'OPEN_QUESTION', 'FINDING')
_INTERPRETATION = 'published Knowledge interpretation'
_PRIMARY = 'published Knowledge original evidence'


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _source(identifier, label, value, provenance, **metadata):
    return {'id': identifier, 'label': label, 'text': value, 'provenance': provenance,
            'content_hash': hashlib.sha256(value.encode()).hexdigest(), **metadata}


def _short(value, limit):
    return isinstance(value, str) and len(value) <= limit


def _selected_evidence(refs):
    """Represent both speakers; list positions do not establish chronology."""
    if len(refs) <= 4:
        return refs
    chosen = set()
    for role in ('user', 'assistant'):
        positions = [index for index, ref in enumerate(refs)
                     if isinstance(ref, dict) and ref.get('role') == role]
        if positions:
            chosen.update((positions[0], positions[-1]))
    for index in range(len(refs)):
        if len(chosen) >= 4:
            break
        chosen.add(index)
    return [ref for index, ref in enumerate(refs) if index in chosen]


def _publication_metadata(db, project_id):
    """Project only correctly typed, bounded identity fields from saved JSON."""
    fields = {'publication_id': 240, 'generated_at': 100,
              'extraction_version': 240, 'fingerprint': 240}
    checks = ["json_type(snapshot)='object'", "json_type(snapshot,'$.items')='array'",
              "json_type(snapshot,'$.sources')='array'",
              "coalesce(json_type(snapshot,'$.project_id') IN ('integer','null'),1)"]
    columns = []
    for field, limit in fields.items():
        kind = f"json_type(snapshot,'$.{field}')"
        value = f"json_extract(snapshot,'$.{field}')"
        checks.extend([f"coalesce({kind} IN ('text','null'),1)", f'coalesce(length({value})<={limit},1)'])
        columns.append(f"CASE WHEN {kind}='text' AND length({value})<={limit} THEN {value} END AS {field}")
    query = ('WITH saved AS (SELECT json_valid(snapshot) AS json_valid, '
             "CASE WHEN json_valid(snapshot) THEN snapshot ELSE '{}' END AS snapshot "
             'FROM project_knowledge WHERE project_id=:pid) '
             'SELECT json_valid AND ' + ' AND '.join(checks) + ' AS valid, '
             "json_extract(snapshot,'$.project_id') AS project_id, " + ','.join(columns) +
             ",json_array_length(snapshot,'$.items') AS count FROM saved")
    row = db.execute(text(query), {'pid': project_id}).mappings().first()
    return dict(row) if row is not None else None


def _origin(db, project_id, ref, descriptor):
    """Return project-authorized navigation and cheap quotation availability.

    Quotation availability is deliberately not a fresh agreement/context check.
    Historical extraction state is retained separately by the caller.
    """
    if any(record.get('project_id') is not None and (
            type(record['project_id']) is not int or record['project_id'] != project_id)
           for record in (ref, descriptor)):
        return None
    source_id = ref['source_id']
    note_match = re.match(r'^chatgpt:note:(\d+):', source_id)
    session_match = re.match(r'^codex:(\d+):', source_id)
    note_id = descriptor.get('note_id')
    session_id = descriptor.get('session_id')
    if note_match:
        if note_id is not None and note_id != int(note_match[1]):
            return None
        note_id = int(note_match[1])
    if session_match:
        if session_id is not None and session_id != int(session_match[1]):
            return None
        session_id = int(session_match[1])
    if note_id is not None and session_id is not None:
        return None
    if isinstance(note_id, int) and not isinstance(note_id, bool) and note_id > 0:
        stable = re.fullmatch(r'chatgpt:note:\d+:mid:([0-9a-f]{24}):p:\d+', source_id)
        ordinal = re.fullmatch(r'chatgpt:note:\d+:m:(\d{1,9}):p:\d+', source_id)
        message_id = descriptor.get('message_id')
        if not (stable and _short(message_id, 512) and _hash(message_id)[:24] == stable[1]):
            message_id = None
        message = "CASE WHEN m.type='object' THEN m.value ELSE '{}' END"
        start = f"json_extract({message},'$.start')"
        end = f"json_extract({message},'$.end')"
        row = db.execute(text(f'WITH note AS (SELECT id,updated_at,reference,content,conversation, '
            f"kind='document' AND {CONVERSATION_NOTE_SQL} AS imported FROM notes WHERE project_id=:pid AND id=:nid) "
            'SELECT id,updated_at,reference,imported,'
            f'(imported AND {CONVERSATION_EXTENT_SQL}>length(content)) AS invalid_extent,'
            'CASE WHEN imported THEN EXISTS (SELECT 1 '
            "FROM json_each(conversation,'$.messages') m WHERE "
            f"((:message_id IS NOT NULL AND json_extract({message},'$.id')=:message_id) OR m.key=:ordinal) "
            f"AND json_extract({message},'$.role')=:role AND json_type({message},'$.start')='integer' "
            f"AND json_type({message},'$.end')='integer' AND {start}>=0 AND {end}>{start} "
            f'AND {end}<=length(content) AND instr(substr(content,{start}+1,{end}-{start}),:quote)>0) '
            'ELSE instr(content,:quote)>0 END AS present FROM note'),
            {'pid': project_id, 'nid': note_id, 'quote': ref['excerpt'], 'role': ref['role'],
             'message_id': message_id, 'ordinal': int(ordinal[1]) - 1 if ordinal else None}).mappings().first()
        if row is None:
            return None
        navigation = {'note_id': note_id}
        if row['imported'] or (isinstance(row['reference'], str) and re.fullmatch(
                r'https://chatgpt\.com/share/[0-9a-f-]{36}/?', row['reference'], re.I)):
            navigation['share'] = True
        validity = 'unavailable' if row['invalid_extent'] else 'available' if row['present'] else 'stale'
        return navigation, validity, {'note_id': note_id, 'updated_at': row['updated_at'],
                                       'quote_present': validity == 'available'}
    if isinstance(session_id, int) and not isinstance(session_id, bool) and session_id > 0:
        session = db.execute(text('SELECT id,is_linked,history_indexed_at,history_indexed_activity,'
                                  'codex_activity_at,last_error FROM codex_sessions '
                                  'WHERE project_id=:pid AND id=:sid'),
                             {'pid': project_id, 'sid': session_id}).mappings().first()
        if session is None:
            return None
        turn_id = descriptor.get('turn_id')
        row = None
        if _short(turn_id, 240) and turn_id:
            row = db.execute(text('SELECT status,length(messages) AS size, '
                'EXISTS(SELECT 1 FROM json_each(CASE WHEN json_valid(messages) '
                "THEN messages ELSE '[]' END) m WHERE m.type='object' "
                "AND json_extract(CASE WHEN m.type='object' THEN m.value ELSE '{}' END,'$.role')=:role "
                "AND instr(json_extract(CASE WHEN m.type='object' THEN m.value ELSE '{}' END,'$.text'),:quote)>0) AS present "
                'FROM codex_history_turns WHERE project_id=:pid AND session_id=:sid AND turn_id=:tid'),
                {'pid': project_id, 'sid': session_id, 'tid': turn_id,
                 'quote': ref['excerpt'], 'role': ref['role']}).mappings().first()
        navigation = {'session_id': session_id}
        if row is not None:
            navigation['turn_id'] = turn_id
        state = ('excluded' if not session['is_linked'] else
                 'unavailable' if row is None or row['status'] == 'inProgress' else 'available' if row['present'] else 'stale')
        marker = dict(session)
        marker['last_error'] = bool(marker['last_error'])
        marker.update(turn_id=turn_id, turn=dict(row) if row is not None else None)
        return navigation, state, marker
    return None


def collect_published_knowledge(db, project_id):
    """Return bounded, atomic interpretation/evidence groups for Scheme only."""
    require_project(db, project_id)
    metadata = {'status': 'absent', 'publication_id': None, 'available_items': 0,
                'included_items': 0, 'omitted_items': 0, 'budget_chars': BUDGET_CHARS,
                'partial_evidence_items': 0}
    result = {'sources': [], 'metadata': metadata, 'marker': None, 'limitations': []}
    row = _publication_metadata(db, project_id)
    if row is None:
        return result
    if not row['valid'] or row['project_id'] not in (None, project_id):
        metadata['status'] = 'unavailable'
        result['marker'] = {'status': 'unavailable'}
        result['limitations'].append('Published Knowledge has invalid project identity or structure and was omitted.')
        return result
    metadata.update(status='published', publication_id=row['publication_id'],
                    generated_at=row['generated_at'], extraction_version=row['extraction_version'],
                    available_items=row['count'] or 0)
    # Per-status ranks preserve a mixture of choices, requirements, proposals and
    # open issues without introducing a fabricated importance/chronology score.
    fields = ('id', 'project_id', 'statement', 'category', 'design_status', 'scope',
              'rationale', 'alternatives', 'limitations', 'source_state', 'evidence',
              'conflicts_with', 'supersedes', 'superseded_by')
    projection = ','.join(f"'{field}',json_extract(value,'$.{field}')" for field in fields)
    rows = db.execute(text('WITH items AS (SELECT e.key AS position,e.value FROM project_knowledge k,'
        "json_each(k.snapshot,'$.items') e WHERE k.project_id=:pid AND e.type='object'), "
        'ranked AS (SELECT position,value,row_number() OVER ('
        "PARTITION BY json_extract(value,'$.design_status') ORDER BY "
        "CASE WHEN json_extract(value,'$.source_state')='valid' THEN 0 ELSE 1 END, "
        "CASE WHEN coalesce(json_array_length(value,'$.superseded_by'),0)=0 THEN 0 ELSE 1 END, "
        "CASE WHEN json_extract(value,'$.category')='REQUIREMENT' THEN 0 ELSE 1 END,position) AS rank "
        'FROM items) SELECT substr(json_object(' + projection + '),1,:chars) AS item FROM ranked '
        "ORDER BY rank,CASE json_extract(value,'$.design_status') WHEN 'AGREED' THEN 0 "
        "WHEN 'UNDER_EVALUATION' THEN 1 WHEN 'UNRESOLVED' THEN 2 WHEN 'PROPOSED' THEN 3 "
        "WHEN 'REJECTED' THEN 4 ELSE 5 END,position LIMIT :lim"),
        {'pid': project_id, 'chars': _MAX_ITEM_CHARS + 1, 'lim': MAX_CANDIDATES}).mappings()
    candidates = []
    for candidate in rows:
        raw = candidate['item']
        if len(raw) > _MAX_ITEM_CHARS:
            continue
        item = json.loads(raw)
        if (item.get('project_id') not in (None, project_id) or not _short(item.get('id'), 240)
                or not item['id'] or item.get('design_status') not in _STATUSES
                or item.get('category') not in _CATEGORIES
                or not _short(item.get('statement'), 600) or not item['statement']
                or not _short(item.get('scope'), 180)
                or not isinstance(item.get('evidence'), list) or not 1 <= len(item['evidence']) <= 6):
            continue
        candidates.append(item)
    identifiers = {ref.get('source_id') for item in candidates for ref in item['evidence']
                   if isinstance(ref, dict) and _short(ref.get('source_id'), 240)}
    descriptors = {}
    if identifiers:
        params = {f'id_{index}': identifier for index, identifier in enumerate(sorted(identifiers))}
        names = ','.join(':' + key for key in params)
        params['pid'] = project_id
        columns = ('id', 'project_id', 'label', 'role', 'revision', 'session_id', 'turn_id', 'note_id', 'message_id')
        source_projection = ','.join(f"'{field}',json_extract(e.value,'$.{field}')" for field in columns)
        source_rows = db.execute(text('SELECT substr(json_object(' + source_projection + '),1,4001) AS source '
            "FROM project_knowledge k,json_each(k.snapshot,'$.sources') e WHERE k.project_id=:pid "
            "AND e.type='object' AND json_extract(e.value,'$.id') IN (" + names + ')'), params)
        for source_row in source_rows:
            if len(source_row.source) <= 4000:
                descriptor = json.loads(source_row.source)
                descriptors.setdefault(descriptor['id'], descriptor)
    used, emitted, availability, origin_markers = 0, set(), {}, []
    for item in candidates:
        evidence, states, origin_keys = [], [], []
        selected_refs = _selected_evidence(item['evidence'])
        # Authorize every stored reference, including those omitted from the
        # compact citation set; partial selection must not conceal foreign data.
        for ref in item['evidence']:
            if (not isinstance(ref, dict) or not _short(ref.get('source_id'), 240)
                    or not _short(ref.get('excerpt'), 1600) or not ref['excerpt']
                    or (ref.get('revision') is not None and not _short(ref['revision'], 240))
                    or ref.get('role') not in ('user', 'assistant')):
                break
            descriptor = descriptors.get(ref['source_id'], {})
            # Omitted descriptor project_id means legacy metadata, not foreign.
            if descriptor.get('project_id') is None:
                descriptor = {key: value for key, value in descriptor.items() if key != 'project_id'}
            origin = _origin(db, project_id, ref, descriptor)
            if origin is None:
                break
            navigation, state, origin_marker = origin
            states.append(state)
            origin_keys.append(origin_marker)
            if ref not in selected_refs:
                continue
            source_id = 'scheme:knowledge:evidence:' + _hash(
                [project_id, ref['source_id'], ref.get('revision'), ref['role'], ref['excerpt']])[:32]
            evidence.append(_source(source_id, str(descriptor.get('label') or ref.get('label') or 'Original conversation')[:240],
                ref['excerpt'], _PRIMARY, original_source_id=ref['source_id'], role=ref['role'],
                evidence_availability=state, **navigation,
                **({'revision': ref['revision']} if ref.get('revision') is not None else {})))
        else:
            evidence = list({source['id']: source for source in evidence}.values())
            state = next((value for value in ('unavailable', 'excluded', 'stale') if value in states), 'available')
            data = {key: item[key] for key in ('id', 'statement', 'category', 'design_status', 'scope')}
            for field in ('rationale', 'alternatives', 'limitations', 'source_state', 'conflicts_with', 'supersedes', 'superseded_by'):
                value = item.get(field)
                if isinstance(value, str) and len(value) <= 800:
                    data[field] = value
                elif isinstance(value, list) and len(value) <= 32 and all(_short(entry, 240) for entry in value):
                    data[field] = value
            data['current_evidence_availability'] = state
            data.update(evidence_refs_available=len(item['evidence']),
                        evidence_refs_selected=len(selected_refs),
                        evidence_partial=len(selected_refs) < len(item['evidence']))
            interpretation_id = 'scheme:knowledge:item:' + _hash([project_id, item['id'], row['publication_id']])[:32]
            interpretation = _source(interpretation_id, 'Published Knowledge · ' + item['statement'][:180],
                json.dumps(data, ensure_ascii=False, sort_keys=True), _INTERPRETATION,
                knowledge_item_id=item['id'], evidence_availability=state,
                evidence_partial=data['evidence_partial'],
                supporting_source_ids=list(dict.fromkeys(source['id'] for source in evidence)))
            group = [interpretation, *[source for source in evidence if source['id'] not in emitted]]
            cost = sum(len(source['text']) for source in group)
            if used + cost > BUDGET_CHARS:
                continue
            result['sources'].extend(group)
            emitted.update(source['id'] for source in group)
            used += cost
            metadata['included_items'] += 1
            metadata['partial_evidence_items'] += int(data['evidence_partial'])
            availability[state] = availability.get(state, 0) + 1
            origin_markers.extend(origin_keys)
    metadata.update(omitted_items=max(0, metadata['available_items'] - metadata['included_items']),
                    input_chars=used, current_evidence_availability=availability)
    if metadata['omitted_items']:
        result['limitations'].append('Published Knowledge context is selected within a bounded budget; some items or unsupported source references were omitted.')
    if metadata['partial_evidence_items']:
        result['limitations'].append('Some Knowledge items have more supporting references than fit a Scheme citation; selected quotations are partial evidence, not support for unshown claims.')
    if any(state != 'available' for state in availability):
        result['limitations'].append('Some published Knowledge quotations are stale, excluded or unavailable; saved design status is historical, not implementation evidence.')
    # SQLite legacy SELECTs do not necessarily pin one read transaction. A
    # publication can be replaced between metadata, item and descriptor reads.
    # Discard the context rather than attach new items to the previous identity.
    current = _publication_metadata(db, project_id)
    if current != row:
        result['sources'] = []
        metadata.update(status='unavailable', included_items=0,
                        omitted_items=metadata['available_items'], input_chars=0,
                        partial_evidence_items=0, current_evidence_availability={})
        result['limitations'].append('Published Knowledge changed during inspection; its context was omitted to preserve publication identity.')
        result['marker'] = {'status': 'unavailable', 'publication_id': row['publication_id'],
                            'current_publication_id': (current or {}).get('publication_id')}
        return result
    result['marker'] = {'publication_id': row['publication_id'], 'fingerprint': row['fingerprint'],
                        'extraction_version': row['extraction_version'], 'generated_at': row['generated_at'],
                        'available_items': metadata['available_items'],
                        'sources': _hash(result['sources']), 'origins': _hash(origin_markers)}
    return result
