"""Bounded, read-only search across records already saved for one project."""
from array import array
import re
from urllib.parse import urlencode

from sqlalchemy import text

from .integrations.codex.history_search import (
    append_search_match, search_history, search_markers, _snippet_parts, _SHARE_REFERENCE,
)
from .integrations.codex.knowledge_sources import CONVERSATION_NOTE_SQL
from .services import require_project

PAGE_SIZE = 10
KINDS = ('tasks', 'notes', 'conversations', 'qa', 'knowledge', 'sessions', 'other')
TITLES = {'tasks': 'TODOs', 'notes': 'Notes', 'conversations': 'Conversations',
          'qa': 'AI Q&A', 'knowledge': 'Knowledge', 'sessions': 'Sessions', 'other': 'Other records'}
_CONVERSATION_NOTE = f"(n.kind='document' AND ({CONVERSATION_NOTE_SQL} OR lower(n.reference) GLOB :share))"


def _page(kind, rows, offset):
    return {'kind': kind, 'title': TITLES[kind], 'results': rows[:PAGE_SIZE],
            'next_offset': offset + PAGE_SIZE if len(rows) > PAGE_SIZE else None}


def _clip_parts(parts, limit=600):
    if len(parts) > 1 and not parts[0]['match'] and (
            len(parts[0]['text']) + min(len(parts[1]['text']), limit) > limit):
        # Recenter before the first match so truncation preserves it when it fits.
        context = min(limit // 5, max(0, limit - len(parts[1]['text']) - 1))
        parts = ([{'text': '…' + parts[0]['text'][-context:], 'match': False}] if context else []) + parts[1:]
    clipped = []
    for part in parts:
        text_ = part['text']
        if clipped and part['match'] and len(text_) > limit:
            clipped.append({'text': '…', 'match': False})
            break
        value = text_[:limit]
        if value:
            clipped.append({**part, 'text': value})
        limit -= len(value)
        if not limit:
            break
    return clipped


def _plain_parts(value, terms, limit=600):
    if not value:
        return []
    folded = value.casefold()
    pattern = '|'.join(re.escape(term) for term in sorted(set(terms), key=lambda term: (-len(term), term)))
    if not pattern:
        return [{'text': value[:limit], 'match': False}]
    offsets = None
    if len(folded) != len(value):
        # Case folding can expand one character (for example ß to ss).
        offsets = array('I', (i for i, char in enumerate(value) for _ in char.casefold()))
    parts, last, stop = [], 0, None
    for hit in re.finditer(pattern, folded):
        left, right = hit.span()
        if offsets is not None:
            left, right = offsets[left], offsets[right - 1] + 1
        if left < last:
            continue
        if stop is None:
            stop = left + limit
        if left >= stop:
            break
        if left > last:
            # Preserve the lead-in needed to decide whether to recenter, even
            # when the match occurs far inside a long document.
            lead = value[max(last, left - limit):left]
            parts.append({'text': lead, 'match': False})
        append_search_match(parts, value[left:right])
        last = right
    if last < len(value):
        parts.append({'text': value[last:last + limit], 'match': False})
    return _clip_parts(parts, limit)


def _text_highlights(title, body, terms):
    return {'title_parts': _plain_parts(title, terms, 500), 'snippet_parts': _plain_parts(body, terms)}


def _fts_highlights(title, body, markers):
    return {'title_parts': _clip_parts(_snippet_parts(title, *markers), 500),
            'snippet_parts': _clip_parts(_snippet_parts(body, *markers))}


def _records(db, project_id, kind, match, offset, markers):
    # Only fixed SQL fragments are interpolated; all values are bound parameters.
    if kind in ('tasks', 'notes'):
        join = f' JOIN {kind} n ON n.id=search_index.entity_id AND n.project_id=search_index.project_id '
        condition = 'search_index.entity_type=:kind'
        if kind == 'notes':
            condition += f' AND NOT {_CONVERSATION_NOTE}'
    else:
        join = ''
        condition = "search_index.entity_type NOT IN ('tasks','notes')"
    rows = db.execute(text(
        "SELECT entity_type,entity_id,search_index.title AS title, "
        "highlight(search_index,3,:highlight_start,:highlight_end) AS marked_title, "
        "snippet(search_index,4,:highlight_start,:highlight_end,'…',40) AS marked_body "
        'FROM search_index' + join + ' WHERE search_index MATCH :match AND search_index.project_id=:pid '
        'AND ' + condition + ' ORDER BY search_index.rank,entity_type,entity_id LIMIT :limit OFFSET :offset'
    ), {'match': match, 'pid': project_id, 'kind': kind, 'share': _SHARE_REFERENCE,
        'limit': PAGE_SIZE + 1, 'offset': offset, 'highlight_start': markers[0],
        'highlight_end': markers[1]}).mappings().all()
    return _page(kind, [{'id': f"{row['entity_type']}:{row['entity_id']}",
                         'title': row['title'][:500],
                         'snippet': row['marked_body'].replace(markers[0], '[').replace(markers[1], ']')[:600],
                         **_fts_highlights(row['marked_title'], row['marked_body'], markers)} for row in rows], offset)


def _contains_terms(expression, terms, params):
    arguments = []
    for i, term in enumerate(terms):
        params[f'term{i}'] = term.casefold()
        arguments.append(f':term{i}')
    return f"projecthub_search_contains({expression}, {', '.join(arguments)})"


def _search_contains(value, *terms):
    # Fold one candidate at a time, without materializing every large note.
    folded = str(value or '').casefold()
    return all(term in folded for term in terms)


def _notes(db, project_id, match, terms, offset, markers):
    params = {'pid': project_id, 'match': match, 'share': _SHARE_REFERENCE,
              'limit': PAGE_SIZE + 1, 'offset': offset, 'highlight_start': markers[0], 'highlight_end': markers[1]}
    where = _contains_terms("n.title || ' ' || n.content || ' ' || n.reference", terms, params)
    # FTS preserves ranked content matches. The second arm also covers references
    # and mixed title/reference queries, excluding FTS hits before pagination.
    rows = db.execute(text(
        "WITH matches AS ("
        "SELECT n.id,n.title,highlight(search_index,3,:highlight_start,:highlight_end) AS marked_title, "
        "snippet(search_index,4,:highlight_start,:highlight_end,'…',40) AS body, search_index.rank AS score "
        "FROM search_index JOIN notes n ON n.id=search_index.entity_id AND n.project_id=search_index.project_id "
        "WHERE search_index MATCH :match AND search_index.entity_type='notes' AND n.project_id=:pid "
        f'AND NOT {_CONVERSATION_NOTE} '
        "UNION ALL SELECT n.id,n.title,NULL,n.content || ' ' || n.reference,0.0 "
        f'FROM notes n WHERE n.project_id=:pid AND NOT {_CONVERSATION_NOTE} '
        "AND n.id NOT IN (SELECT entity_id FROM search_index WHERE search_index MATCH :match "
        "AND project_id=:pid AND entity_type='notes') AND " + where + ') '
        "SELECT id,title,marked_title,body FROM matches ORDER BY score,id LIMIT :limit OFFSET :offset"
    ), params).mappings().all()
    results = []
    for row in rows:
        title, body = row['title'], row['body']
        if row['marked_title'] is None:
            highlights = _text_highlights(title, body, terms)
        else:
            highlights = _fts_highlights(row['marked_title'], body, markers)
            body = body.replace(markers[0], '[').replace(markers[1], ']')
        results.append({'id': f"notes:{row['id']}", 'title': title[:500], 'snippet': body[:600], **highlights})
    return _page('notes', results, offset)


def _qa(db, project_id, terms, offset):
    # JSON is expanded inside SQLite so only the selected result page reaches Python.
    answer = "coalesce(json_extract(item.value,'$.answer'),'')"
    variants = "coalesce((SELECT group_concat(json_extract(v.value,'$.answer'),' ') FROM json_each(item.value,'$.variants') v),'')"
    question = "coalesce(json_extract(item.value,'$.question'),'')"
    body = answer + " || ' ' || " + variants
    params = {'pid': project_id, 'limit': PAGE_SIZE + 1, 'offset': offset}
    where = _contains_terms(question + " || ' ' || " + body, terms, params)
    rows = db.execute(text(
        f'SELECT item.key AS position, {question} AS title, {body} AS snippet '
        'FROM project_qa q, json_each(q.items) item WHERE q.project_id=:pid AND '
        + where + ' ORDER BY CAST(item.key AS INTEGER) LIMIT :limit OFFSET :offset'
    ), params).mappings().all()
    return _page('qa', [{'id': f"qa:{row['position']}", 'title': row['title'][:500], 'snippet': row['snippet'][:600],
                        **_text_highlights(row['title'], row['snippet'], terms)}
                        for row in rows], offset)


def _knowledge(db, project_id, terms, offset):
    fields = ('statement', 'rationale', 'alternatives', 'limitations', 'scope', 'category', 'design_status')
    fields_sql = " || ' ' || ".join(f"coalesce(json_extract(item.value,'$.{field}'),'')" for field in fields)
    evidence = """coalesce((SELECT group_concat(
        coalesce(json_extract(e.value,'$.excerpt'),'') || ' ' || coalesce(json_extract(e.value,'$.label'),'') || ' ' ||
        coalesce(labels.label,'') ,' ')
        FROM json_each(item.value,'$.evidence') e
        LEFT JOIN source_labels labels ON labels.id=json_extract(e.value,'$.source_id')),'')"""
    body = fields_sql + " || ' ' || " + evidence
    params = {'pid': project_id, 'limit': PAGE_SIZE + 1, 'offset': offset}
    where = _contains_terms(body, terms, params)
    rows = db.execute(text(
        # Materialize and index source labels once, rather than rescanning the
        # complete source array for every evidence reference of every item.
        # If malformed legacy data repeats an ID, retain the first valid source
        # just as the previous LIMIT 1 lookup did.
        "WITH source_labels AS MATERIALIZED ("
        "SELECT id,label FROM (SELECT json_extract(s.value,'$.id') AS id, "
        "json_extract(s.value,'$.label') AS label, "
        "row_number() OVER (PARTITION BY json_extract(s.value,'$.id') ORDER BY CAST(s.key AS INTEGER)) AS ordinal "
        "FROM project_knowledge owner, json_each(owner.snapshot,'$.sources') s "
        "WHERE owner.project_id=:pid AND json_extract(owner.snapshot,'$.project_id')=:pid "
        "AND (json_extract(s.value,'$.project_id') IS NULL OR json_extract(s.value,'$.project_id')=:pid)) "
        "WHERE ordinal=1) "
        "SELECT json_extract(item.value,'$.id') AS id, "
        "json_extract(k.snapshot,'$.publication_id') AS publication, "
        "CAST(json_extract(item.value,'$.statement') AS TEXT) AS title, "
        f"{body} AS snippet FROM project_knowledge k, json_each(k.snapshot,'$.items') item "
        "WHERE k.project_id=:pid AND json_extract(k.snapshot,'$.project_id')=:pid "
        "AND (json_extract(item.value,'$.project_id') IS NULL OR json_extract(item.value,'$.project_id')=:pid) "
        'AND ' + where + ' ORDER BY CAST(item.key AS INTEGER) LIMIT :limit OFFSET :offset'
    ), params).mappings().all()
    results = []
    for row in rows:
        params = {'project': project_id, 'item': row['id']}
        if row['publication']:
            params['publication'] = row['publication']
        title, snippet = row['title'], row['snippet']
        results.append({'id': 'knowledge:' + str(row['id']), 'title': title[:500] if title is not None else None,
                        'snippet': snippet[:600], **_text_highlights(str(title or ''), snippet, terms),
                        'href': '#knowledge?' + urlencode(params)})
    return _page('knowledge', results, offset)


def _sessions(db, project_id, terms, offset):
    params = {'pid': project_id, 'limit': PAGE_SIZE + 1, 'offset': offset}
    where = _contains_terms("title || ' ' || thread_id || ' ' || topic || ' ' || CAST(id AS TEXT) || ' ' || "
                            "coalesce(codex_account,'') || ' ' || coalesce(status,'')", terms, params)
    rows = db.execute(text(
        'SELECT id,substr(title,1,500) AS title,thread_id,substr(topic,1,300) AS topic FROM codex_sessions '
        'WHERE project_id=:pid AND is_linked=1 AND ' + where + ' ORDER BY id DESC LIMIT :limit OFFSET :offset'
    ), params).mappings().all()
    results = []
    for row in rows:
        title = row['title'] or 'Untitled session'
        snippet = f"{row['topic']} · {row['thread_id']}".strip(' ·')
        results.append({'id': f"session:{row['id']}", 'title': title, 'snippet': snippet,
                        **_text_highlights(title, snippet, terms)})
    return _page('sessions', results, offset)


def project_search(db, project_id, query, kind='all', offset=0):
    require_project(db, project_id)
    # SQLite lower() handles ASCII only. A connection-local pure function keeps
    # JSON/session searches case-insensitive for Unicode without changing stored data.
    db.connection().connection.driver_connection.create_function(
        'projecthub_search_contains', -1, _search_contains, deterministic=True)
    terms = re.findall(r'\w+', query, flags=re.UNICODE)[:30]
    selected = KINDS if kind == 'all' else (kind,)
    if not terms:
        return {'groups': [{'kind': key, 'title': TITLES[key], 'results': [], 'next_offset': None}
                           for key in selected]}
    match = ' AND '.join('"' + term + '"' for term in terms)
    terms = [term.casefold() for term in terms]
    markers = search_markers()
    groups = []
    for key in selected:
        start = 0 if kind == 'all' else offset
        if key == 'notes':
            groups.append(_notes(db, project_id, match, terms, start, markers))
        elif key in ('tasks', 'other'):
            groups.append(_records(db, project_id, key, match, start, markers))
        elif key == 'qa':
            groups.append(_qa(db, project_id, terms, start))
        elif key == 'knowledge':
            groups.append(_knowledge(db, project_id, terms, start))
        elif key == 'sessions':
            groups.append(_sessions(db, project_id, terms, start))
        else:
            page = search_history(db, project_id, query, start, include_shared=True)
            rows = []
            for item in page['results']:
                shared = item['kind'] == 'chatgpt'
                params = {'project': project_id, **({'note': item['note_id'], 'q': query} if shared else
                                                   {'session': item['session_id'], 'turn': item['turn_id']})}
                title = (item['title'] if shared else item['session_title'] or 'Untitled session')[:500]
                rows.append({'id': f"chatgpt:{item['note_id']}" if shared else f"codex:{item['session_id']}:{item['turn_id']}",
                             'title': title, 'title_parts': _plain_parts(title, terms, 500),
                             'snippet': item['snippet'][:600],
                             'snippet_parts': _clip_parts(item['snippet_parts']),
                             'href': '#history?' + urlencode(params)})
            groups.append({'kind': key, 'title': TITLES[key], 'results': rows, 'next_offset': page['next_offset']})
    return {'groups': groups}
