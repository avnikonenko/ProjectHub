"""Saved evidence measurements; absence means unknown, never complete coverage."""
from copy import deepcopy


def conversation_coverage(bundle, diagnostics):
    entries = bundle.get('manifest', {}).get('entries', [])
    keys = {entry['source_key'] for entry in entries if isinstance(entry.get('source_key'), str)}
    coverage = bundle.get('coverage', {})
    def count(name):
        return len(set(coverage.get(name, [])) & keys)
    result = {
        'inventory_scope': 'saved_source_manifest', 'known_sources': len(keys),
        'processed_sources': count('processed'), 'partial_sources': count('unprocessed'),
        'excluded_sources': count('excluded'), 'unavailable_sources': count('unavailable'),
        'notes': ['Counts describe known local conversation source groups considered for this run, including unavailable or historically excluded records, not every remote conversation.',
                  'Selected fragments were supplied for extraction or reused from a matching extraction cache; this does not guarantee every detail survived interpretation.'],
    }
    selection = diagnostics.get('source_selection', {})
    for target, source in (('available_fragments', 'available_primary_fragments'),
                           ('selected_fragments', 'selected_primary_fragments'),
                           ('omitted_fragments', 'omitted_primary_fragments')):
        if type(selection.get(source)) is int and selection[source] >= 0:
            result[target] = selection[source]
    return result


def repository_coverage(repository):
    measured = repository.get('inspection') if isinstance(repository, dict) else None
    if not isinstance(measured, dict):
        return {'status': 'unrecorded', 'notes': ['Exact file coverage was not recorded for this analysis.']}
    result = deepcopy(measured)
    result['notes'].append('Input excerpts were supplied directly or through matching cached synthesis stages; not every input is cited in the final text.')
    return result


def knowledge_coverage(bundle, diagnostics):
    return {
        'conversations': conversation_coverage(bundle, diagnostics),
        'repository': {'status': 'not_used', 'read_files': 0, 'ai_input_files': 0,
                       'notes': ['Knowledge records were extracted from conversations. Repository excerpts inform the separate summary, not these records.']},
    }
