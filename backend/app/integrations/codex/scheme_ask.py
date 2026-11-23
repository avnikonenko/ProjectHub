"""Bounded, source-linked inputs for questions about a saved Scheme."""
import json
import re

from sqlalchemy.exc import SQLAlchemyError

from ...config import ConfigError, scheme_inspection_limits
from ...project_evidence import EvidenceReadError
from .scheme_evidence import _EvidenceScope, collect_evidence


_SURROGATES = re.compile('[\ud800-\udfff]')
_REPOSITORY_PROVENANCE = {'observed in code', 'repository test source', 'repository configuration',
                          'repository file structure', 'project documentation', 'dependency manifest'}
_NAVIGATION_FIELDS = ('session_id', 'turn_id', 'note_id', 'note_created_at', 'share', 'shared_sessions')


def _clip(value, limit):
    if not isinstance(value, str):
        return ''
    return _SURROGATES.sub('\ufffd', value if len(value) <= limit else value[:limit - 1] + '…')


def _rows(rows):
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


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
        def rank(value):
            return scope.score(' '.join(str(value.get(field, '')) for field in fields) + ' ' + ' '.join(
                node_labels.get(value.get(field), '') for field in ('source', 'target')))

        encoded_lengths = [len(_json(value)) + 1 for value in values]
        selected = set()
        for index in sorted(range(len(values)), key=lambda idx: -rank(values[idx])):
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


def saved_scheme_passages(snapshot, kind, question):
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

    references = {ref for _, value in values for rows in value.values() if isinstance(rows, list)
                  for row in rows if isinstance(row, dict) for ref in row.get('source_ids', [])}
    evidence = _rows(snapshot.get('sources'))
    inventory = snapshot.get('dependency_inventory')
    if isinstance(inventory, dict):
        evidence.extend(_rows(inventory.get('sources')))
    seen = set()
    selected = []
    for source in sorted(evidence, key=lambda item: (
            not isinstance(item.get('id'), str) or item['id'] not in references,
            -scope.score(_clip(item.get('label'), 240) + ' ' + _clip(item.get('text'), 1800)))):
        identifier = source.get('id')
        excerpt = _clip(source.get('text'), 1800)
        if not isinstance(identifier, str) or not excerpt.strip() or identifier in seen:
            continue
        seen.add(identifier)
        passage = {'scheme_source_id': _clip(identifier, 200), 'kind': 'saved_scheme_evidence',
                   'label': 'Saved evidence · ' + (_clip(source.get('label'), 190) or 'Project passage'),
                   'text': excerpt}
        for key in ('provenance', 'path', 'evidence_availability'):
            if isinstance(source.get(key), str):
                passage[key] = _clip(source[key], 300)
        if source.get('evidence_availability') not in ('unavailable', 'excluded'):
            passage.update({key: source[key] for key in _NAVIGATION_FIELDS if key in source})
        selected.append(passage)
        if len(selected) >= 12:
            break
    if references - seen:
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
        inspection = collect_evidence(engine, project_id, limits, scope=question)
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
    return passages
