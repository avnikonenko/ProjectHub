"""Bounded, read-only search across records already saved for one project."""
import re
from urllib.parse import urlencode

from sqlalchemy import text

from .integrations.codex.history_search import search_history, _SHARE_REFERENCE
from .services import require_project

PAGE_SIZE = 10
KINDS = ('tasks', 'notes', 'conversations', 'qa', 'knowledge', 'sessions', 'other')
TITLES = {'tasks': 'TODOs', 'notes': 'Notes', 'conversations': 'Conversations',
          'qa': 'AI Q&A', 'knowledge': 'Knowledge', 'sessions': 'Sessions', 'other': 'Other records'}


def _page(kind, rows, offset):
    return {'kind': kind, 'title': TITLES[kind], 'results': rows[:PAGE_SIZE],
            'next_offset': offset + PAGE_SIZE if len(rows) > PAGE_SIZE else None}


def _records(db, project_id, kind, match, offset):
    # Only fixed SQL fragments are interpolated; all values are bound parameters.
    if kind in ('tasks', 'notes'):
        join = f' JOIN {kind} n ON n.id=search_index.entity_id AND n.project_id=search_index.project_id '
        condition = 'search_index.entity_type=:kind'
        if kind == 'notes':
            condition += " AND NOT (n.kind='document' AND lower(n.reference) GLOB :share)"
    else:
        join = ''
        condition = "search_index.entity_type NOT IN ('tasks','notes')"
    rows = db.execute(text(
        "SELECT entity_type,entity_id,search_index.title AS title, "
        "substr(snippet(search_index,4,'[',']','…',40),1,600) AS snippet "
        'FROM search_index' + join + ' WHERE search_index MATCH :match AND search_index.project_id=:pid '
        'AND ' + condition + ' ORDER BY search_index.rank,entity_type,entity_id LIMIT :limit OFFSET :offset'
    ), {'match': match, 'pid': project_id, 'kind': kind, 'share': _SHARE_REFERENCE,
        'limit': PAGE_SIZE + 1, 'offset': offset}).mappings().all()
    return _page(kind, [{'id': f"{row['entity_type']}:{row['entity_id']}",
                         'title': row['title'][:500], 'snippet': row['snippet']} for row in rows], offset)


def _contains_terms(expression, terms, params):
    clauses = []
    for i, term in enumerate(terms):
        params[f'term{i}'] = term.casefold()
        clauses.append(f'instr(projecthub_search_fold({expression}), :term{i}) > 0')
    return ' AND '.join(clauses)


def _notes(db, project_id, match, terms, offset):
    params = {'pid': project_id, 'match': match, 'share': _SHARE_REFERENCE,
              'limit': PAGE_SIZE + 1, 'offset': offset}
    where = _contains_terms("n.title || ' ' || n.content || ' ' || n.reference", terms, params)
    # FTS preserves ranked content matches. The second arm also covers references
    # and mixed title/reference queries, excluding FTS hits before pagination.
    rows = db.execute(text(
        "WITH matches AS ("
        "SELECT n.id,substr(n.title,1,500) AS title, "
        "substr(snippet(search_index,4,'[',']','…',40),1,600) AS snippet, search_index.rank AS score "
        "FROM search_index JOIN notes n ON n.id=search_index.entity_id AND n.project_id=search_index.project_id "
        "WHERE search_index MATCH :match AND search_index.entity_type='notes' AND n.project_id=:pid "
        "AND NOT (n.kind='document' AND lower(n.reference) GLOB :share) "
        "UNION ALL SELECT n.id,substr(n.title,1,500),substr(n.content || ' ' || n.reference,1,600),0.0 "
        "FROM notes n WHERE n.project_id=:pid AND NOT (n.kind='document' AND lower(n.reference) GLOB :share) "
        'AND ' + where + " AND n.id NOT IN (SELECT entity_id FROM search_index WHERE search_index MATCH :match "
        "AND project_id=:pid AND entity_type='notes')) "
        "SELECT id,title,snippet FROM matches ORDER BY score,id LIMIT :limit OFFSET :offset"
    ), params).mappings().all()
    return _page('notes', [{'id': f"notes:{row['id']}", 'title': row['title'], 'snippet': row['snippet']}
                           for row in rows], offset)


def _qa(db, project_id, terms, offset):
    # JSON is expanded inside SQLite so only the selected result page reaches Python.
    answer = "coalesce(json_extract(item.value,'$.answer'),'')"
    variants = "coalesce((SELECT group_concat(json_extract(v.value,'$.answer'),' ') FROM json_each(item.value,'$.variants') v),'')"
    question = "coalesce(json_extract(item.value,'$.question'),'')"
    body = answer + " || ' ' || " + variants
    params = {'pid': project_id, 'limit': PAGE_SIZE + 1, 'offset': offset}
    where = _contains_terms(question + " || ' ' || " + body, terms, params)
    rows = db.execute(text(
        f'SELECT item.key AS position, substr({question},1,500) AS title, substr({body},1,600) AS snippet '
        'FROM project_qa q, json_each(q.items) item WHERE q.project_id=:pid AND '
        + where + ' ORDER BY CAST(item.key AS INTEGER) LIMIT :limit OFFSET :offset'
    ), params).mappings().all()
    return _page('qa', [{'id': f"qa:{row['position']}", 'title': row['title'], 'snippet': row['snippet']}
                        for row in rows], offset)


def _knowledge(db, project_id, terms, offset):
    fields = ('statement', 'rationale', 'alternatives', 'limitations', 'scope', 'category', 'design_status')
    fields_sql = " || ' ' || ".join(f"coalesce(json_extract(item.value,'$.{field}'),'')" for field in fields)
    evidence = """coalesce((SELECT group_concat(
        coalesce(json_extract(e.value,'$.excerpt'),'') || ' ' || coalesce(json_extract(e.value,'$.label'),'') || ' ' ||
        coalesce((SELECT json_extract(s.value,'$.label') FROM json_each(k.snapshot,'$.sources') s
                  WHERE json_extract(s.value,'$.id')=json_extract(e.value,'$.source_id')
                  AND (json_extract(s.value,'$.project_id') IS NULL OR json_extract(s.value,'$.project_id')=:pid)
                  LIMIT 1),'') ,' ')
        FROM json_each(item.value,'$.evidence') e),'')"""
    body = fields_sql + " || ' ' || " + evidence
    params = {'pid': project_id, 'limit': PAGE_SIZE + 1, 'offset': offset}
    where = _contains_terms(body, terms, params)
    rows = db.execute(text(
        "SELECT json_extract(item.value,'$.id') AS id, "
        "json_extract(k.snapshot,'$.publication_id') AS publication, "
        "substr(json_extract(item.value,'$.statement'),1,500) AS title, "
        f"substr({body},1,600) AS snippet FROM project_knowledge k, json_each(k.snapshot,'$.items') item "
        "WHERE k.project_id=:pid AND json_extract(k.snapshot,'$.project_id')=:pid "
        "AND (json_extract(item.value,'$.project_id') IS NULL OR json_extract(item.value,'$.project_id')=:pid) "
        'AND ' + where + ' ORDER BY CAST(item.key AS INTEGER) LIMIT :limit OFFSET :offset'
    ), params).mappings().all()
    results = []
    for row in rows:
        params = {'project': project_id, 'item': row['id']}
        if row['publication']:
            params['publication'] = row['publication']
        results.append({'id': 'knowledge:' + str(row['id']), 'title': row['title'], 'snippet': row['snippet'],
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
    return _page('sessions', [{'id': f"session:{row['id']}", 'title': row['title'] or 'Untitled session',
                              'snippet': f"{row['topic']} · {row['thread_id']}".strip(' ·')}
                             for row in rows], offset)


def project_search(db, project_id, query, kind='all', offset=0):
    require_project(db, project_id)
    # SQLite lower() handles ASCII only. A connection-local pure function keeps
    # JSON/session searches case-insensitive for Unicode without changing stored data.
    db.connection().connection.driver_connection.create_function(
        'projecthub_search_fold', 1, lambda value: str(value or '').casefold(), deterministic=True)
    terms = re.findall(r'\w+', query, flags=re.UNICODE)[:30]
    selected = KINDS if kind == 'all' else (kind,)
    if not terms:
        return {'groups': [{'kind': key, 'title': TITLES[key], 'results': [], 'next_offset': None}
                           for key in selected]}
    match = ' AND '.join('"' + term + '"' for term in terms)
    groups = []
    for key in selected:
        start = 0 if kind == 'all' else offset
        if key == 'notes':
            groups.append(_notes(db, project_id, match, terms, start))
        elif key in ('tasks', 'other'):
            groups.append(_records(db, project_id, key, match, start))
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
                rows.append({'id': f"chatgpt:{item['note_id']}" if shared else f"codex:{item['session_id']}:{item['turn_id']}",
                             'title': (item['title'] if shared else item['session_title'] or 'Untitled session')[:500],
                             'snippet': item['snippet'][:600], 'href': '#history?' + urlencode(params)})
            groups.append({'kind': key, 'title': TITLES[key], 'results': rows, 'next_offset': page['next_offset']})
    return {'groups': groups}
