"""Bounded, source-linked inputs for questions about a saved Scheme."""
import json
import re

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ...config import ConfigError, scheme_inspection_limits
from ...project_evidence import EvidenceReadError
from .history_search import _SHARE_REFERENCE
from .knowledge_sources import CONVERSATION_NOTE_SQL
from .scheme_evidence import _EvidenceScope, collect_evidence
from .scheme_passages import EXCERPT_CHARS, GAP, select_passages


_SURROGATES = re.compile('[\ud800-\udfff]')
_REPOSITORY_PROVENANCE = {'observed in code', 'repository test source', 'repository configuration',
                          'repository file structure', 'project documentation', 'dependency manifest'}
_NOTE_LINK = text(f"""SELECT 1 FROM notes WHERE project_id=:pid AND id=:nid AND created_at=:created
    AND kind='document' AND ({CONVERSATION_NOTE_SQL} OR lower(reference) GLOB :share)
    AND (instr(content,:excerpt)>0 OR updated_at=:updated)""")
_TURN_LINK = text("""SELECT 1 FROM codex_history_turns h JOIN codex_sessions s
    ON s.project_id=h.project_id AND s.id=h.session_id AND s.is_linked=1
    WHERE h.project_id=:pid AND h.session_id=:sid AND h.turn_id=:tid AND h.status!='inProgress'
    AND CASE WHEN :spans IS NULL THEN instr(h.content,:excerpt)>0 ELSE NOT EXISTS (
        SELECT 1 FROM json_each(:spans) part
        WHERE substr(h.content, json_extract(part.value,'$[0]') + 1, json_extract(part.value,'$[1]'))
              IS NOT json_extract(part.value,'$[2]')
    ) END""")


def _clip(value, limit):
    if not isinstance(value, str):
        return ''
    return _SURROGATES.sub('\ufffd', value if len(value) <= limit else value[:limit - 1] + '…')


def _rows(rows):
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _turn_link_spans(source):
    """Recover exact source slices from their offsets and rendered lengths."""
    spans = source.get('excerpt_spans')
    if not isinstance(spans, list) or not 1 < len(spans) <= 64:
        return None
    excerpt = source['text']
    cursor, previous_end = 0, -1
    pieces = []
    for span in spans:
        if not isinstance(span, dict):
            return None
        start, end = span.get('start'), span.get('end')
        if type(start) is not int or type(end) is not int or not previous_end < start < end < 2**63:
            return None
        width = end - start
        piece_end = cursor + width
        if piece_end > len(excerpt):
            return None
        pieces.append((start, width, excerpt[cursor:piece_end]))
        if piece_end < len(excerpt) and not excerpt.startswith(GAP, piece_end):
            return None
        cursor = piece_end + len(GAP)
        previous_end = end
    return _json(pieces) if cursor == len(excerpt) + len(GAP) else None


def _saved_excerpt(source, scope):
    value = _clip(source.get('text'), 24_000)
    prefix = _clip(value, 1800)
    if len(value) <= 1800:
        return prefix
    selected = select_passages(value, 1800, priority=scope.score)['text']
    return selected if scope.score(selected) > scope.score(prefix) else prefix


def _entry_score(row, scope, labels):
    terms = [value for value in row.values() if isinstance(value, str)]
    terms.extend(labels.get(row.get(key), '') for key in ('source', 'target'))
    return scope.score(' '.join(terms))


def _saved_graph(graph, scope):
    result, remaining = {}, 18_000
    node_labels = {row['id']: _clip(row.get('label'), 100) for row in _rows(graph.get('nodes'))
                   if isinstance(row.get('id'), str)}
    for name, fields, reserve in (
        ('nodes', {'id': 64, 'label': 100, 'description': 400, 'type': 64, 'status': 32}, 6_000),
        ('edges', {'source': 64, 'target': 64, 'label': 100, 'type': 50, 'status': 32}, 0),
    ):
        entries = _rows(graph.get(name))
        included_nodes = {node.get('id') for node in result.get('nodes', [])}
        values = []
        for entry in entries:
            value = {field: _clip(entry[field], limit) for field, limit in fields.items()
                     if isinstance(entry.get(field), str)}
            refs = entry.get('source_ids')
            if isinstance(refs, list):
                value['source_ids'] = [_clip(ref, 200) for ref in refs[:6] if isinstance(ref, str)]
            if name == 'edges':
                for endpoint in ('source', 'target'):
                    identifier = value.get(endpoint)
                    if identifier in node_labels and identifier not in included_nodes:
                        value[f'{endpoint}_label'] = node_labels[identifier]
            values.append(value)
        encoded_lengths = [len(_json(value)) + 1 for value in values]
        selected = set()
        for index in sorted(range(len(values)), key=lambda idx: -_entry_score(values[idx], scope, node_labels)):
            size = encoded_lengths[index]
            if size <= remaining - reserve:
                selected.add(index)
                remaining -= size
        result[name] = [value for index, value in enumerate(values) if index in selected]
        if len(result[name]) < len(entries):
            result[f'omitted_{name}'] = len(entries) - len(result[name])
    result['explanation'] = _clip(graph.get('explanation'), 1000)
    return result


def _saved_dependencies(snapshot, scope):
    inventory = snapshot.get('dependency_inventory')
    saved = inventory.get('dependencies') if isinstance(inventory, dict) else None
    rows = _rows(saved if isinstance(saved, list) else snapshot.get('dependencies'))
    rows.sort(key=lambda row: -scope.score(' '.join(
        _clip(row.get(field), 200) for field in ('name', 'category', 'purpose'))))
    dependencies = []
    for row in rows[:20]:
        entry = {key: _clip(row[key], 200) for key in ('name', 'constraint', 'category', 'purpose')
                 if isinstance(row.get(key), str)}
        refs = []
        for key in ('source_ids', 'purpose_source_ids'):
            if isinstance(row.get(key), list):
                refs.extend(_clip(ref, 200) for ref in row[key][:6] if isinstance(ref, str))
        entry['source_ids'] = list(dict.fromkeys(refs))
        dependencies.append(entry)
        if len(_json(dependencies)) > 6_000:
            dependencies.pop()
            break
    return {'dependencies': dependencies, 'omitted_dependencies': len(rows) - len(dependencies)}


def _current_navigation(db, project_id, source):
    if db is None or source.get('evidence_availability') in ('unavailable', 'excluded'):
        return {}
    excerpt = source['text']
    if len(excerpt) > 24_000 or _SURROGATES.search(excerpt):
        return {}
    note_id, created = source.get('note_id'), source.get('note_created_at')
    updated = source.get('date')
    updated = updated if _clip(updated, 128) == updated else None
    if (source.get('share') is True and type(note_id) is int and 0 < note_id < 2**63
            and isinstance(created, str) and created and _clip(created, 128) == created
            and db.scalar(_NOTE_LINK, {'pid': project_id, 'nid': note_id, 'created': created,
                                       'share': _SHARE_REFERENCE, 'excerpt': excerpt,
                                       'updated': updated})):
        return {'note_id': note_id, 'note_created_at': created, 'share': True}

    spans = _turn_link_spans(source)

    def turn(record):
        session_id, turn_id = record.get('session_id'), record.get('turn_id')
        if (type(session_id) is int and 0 < session_id < 2**63 and isinstance(turn_id, str)
                and 0 < len(turn_id) <= 200 and not _SURROGATES.search(turn_id)
                and db.scalar(_TURN_LINK, {'pid': project_id, 'sid': session_id, 'tid': turn_id,
                                           'excerpt': excerpt, 'spans': spans})):
            return {'session_id': session_id, 'turn_id': turn_id}
        return {}

    navigation = turn(source)
    shared = source.get('shared_sessions')
    if isinstance(shared, list):
        alternates = [{**linked, 'session_title': _clip(record.get('session_title'), 200)}
                      for record in _rows(shared[:4]) if (linked := turn(record))]
        if alternates:
            navigation['shared_sessions'] = alternates
    return navigation


def saved_scheme_passages(snapshot, kind, question, *, db=None, project_id=None):
    title = 'Saved Focused Scheme' if kind == 'focused_scheme' else 'Saved Scheme'
    coverage = snapshot.get('coverage')
    coverage = coverage if isinstance(coverage, dict) else {}
    limitations = coverage.get('limitations')
    context = {'generated_at': snapshot['generated_at'], 'partial_coverage': bool(coverage.get('partial')),
               'limitations': [_clip(text, 450) for text in limitations[:10] if isinstance(text, str)]
               if isinstance(limitations, list) else []}
    if isinstance(snapshot.get('instruction'), str):
        context['instruction'] = _clip(snapshot['instruction'], 2000)
    missing = _rows(coverage.get('requested_file_omissions'))
    if missing:
        context['requested_files_omitted'] = [{key: _clip(row.get(key), 300) for key in ('path', 'reason')}
                                              for row in missing[:6]]
    values = [('Coverage and version', context)]
    graphs = (('graph', 'Diagram'),) if kind == 'focused_scheme' else (('architecture', 'Architecture'), ('workflow', 'Workflow'))
    scope = _EvidenceScope(question)
    for key, label in graphs:
        if isinstance(snapshot.get(key), dict):
            values.append((label, _saved_graph(snapshot[key], scope)))
    dependencies = _saved_dependencies(snapshot, scope)
    if dependencies['dependencies']:
        values.append(('Dependencies', dependencies))

    references = {}
    for _, value in values:
        labels = {node['id']: node.get('label', '') for node in value.get('nodes', []) if 'id' in node}
        for section in ('nodes', 'edges', 'dependencies'):
            for row in value.get(section, []):
                score = _entry_score(row, scope, labels)
                for ref in row.get('source_ids', []):
                    references[ref] = max(references.get(ref, 0), score)
    evidence = _rows(snapshot.get('sources'))
    inventory = snapshot.get('dependency_inventory')
    if isinstance(inventory, dict):
        evidence.extend(_rows(inventory.get('sources')))
    seen = set()
    selected = []
    def rank(item):
        identifier = _clip(item.get('id'), 200)
        return (-references.get(identifier, 0),
                -scope.score(_clip(item.get('label'), 240) + ' ' + _clip(item.get('text'), EXCERPT_CHARS)),
                identifier not in references)

    for source in sorted(evidence, key=rank):
        identifier = source.get('id')
        if not isinstance(identifier, str) or identifier in seen:
            continue
        excerpt = _saved_excerpt(source, scope)
        if not excerpt.strip():
            continue
        seen.add(identifier)
        passage = {'scheme_source_id': _clip(identifier, 200), 'kind': 'saved_scheme_evidence',
                   'label': 'Saved evidence · ' + (_clip(source.get('label'), 190) or 'Project passage'),
                   'text': excerpt}
        for key in ('provenance', 'path', 'evidence_availability'):
            if isinstance(source.get(key), str):
                passage[key] = _clip(source[key], 300)
        if 'evidence_availability' in passage:
            passage['evidence_availability_as_of'] = snapshot['generated_at']
        passage.update(_current_navigation(db, project_id, source))
        selected.append(passage)
        if len(selected) >= 12:
            break
    if references.keys() - {_clip(identifier, 200) for identifier in seen}:
        context['limitations'].append('Some source excerpts cited in the diagram were outside this question’s context budget.')
    return ([{'kind': 'saved_scheme_interpretation', 'label': f'{title} · {label}', 'text': _json(value)}
             for label, value in values] + selected)


def current_repository_passages(engine, project_id, question):
    """Keep only relevant repository evidence from the existing local inspector."""
    scope = _EvidenceScope(question)
    if not (scope.terms or scope.paths):
        return []
    try:
        limits = tuple(min(value, cap) for value, cap in zip(scheme_inspection_limits(), (24_000, 1_000_000, 96_000)))
        inspection = collect_evidence(engine, project_id, limits, scope=question, persist_cache=False)
    except (ConfigError, EvidenceReadError, OSError, SQLAlchemyError):
        return [{'kind': 'current_repository_status', 'label': 'Current repository lookup',
                 'text': 'Current repository evidence was unavailable for this question.'}]
    passages = []
    for source in _rows(inspection.get('sources')):
        path = _clip(source.get('path'), 300)
        excerpt = _clip(source.get('text'), 1400)
        provenance = source.get('provenance')
        if (path and excerpt and provenance in _REPOSITORY_PROVENANCE
                and (any(scope.path_rank(path)) or scope.score(excerpt))):
            passages.append({'kind': 'current_repository_evidence', 'path': path, 'text': excerpt,
                             'label': 'Current repository · ' + path, 'provenance': provenance})
            if len(passages) == 4:
                break
    coverage = inspection.get('coverage') or {}
    omissions = _rows(coverage.get('requested_file_omissions'))
    files = coverage.get('file_counts') or {}
    if omissions:
        detail = _json([{'path': _clip(row.get('path'), 300), 'reason': _clip(row.get('reason'), 100)}
                        for row in omissions[:6]])
        passages.append({'kind': 'current_repository_status', 'label': 'Current repository lookup',
                         'text': 'No current excerpt was supplied for these requested files: ' + detail})
    elif not passages and files.get('listing_complete') is False:
        passages.append({'kind': 'current_repository_status', 'label': 'Current repository lookup',
                         'text': 'The current repository could not be fully inspected for this question.'})
    return passages
