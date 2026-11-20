"""Coverage propagation fixtures; no model calls or semantic-quality assertions."""
from copy import deepcopy
from types import SimpleNamespace

from backend.app.generation_profiles import get_generation_profile
from backend.app.integrations.codex import archive, knowledge, scheme
from backend.app.integrations.codex.knowledge_profiles import use_knowledge_profile


PARTIAL = 'Conversation summary coverage is partial: omitted 1 point with invalid citations.'


def package():
    return {'sources': [
                {'id': 'file:main', 'text': 'A source module.', 'provenance': 'Observed in code'},
                {'id': 'discussion:original', 'text': 'Keep the original source.',
                 'provenance': 'Codex conversation'},
            ], 'dependencies': [], 'tree': [],
            'coverage': {'partial': False, 'limitations': ['Original coverage.'],
                         'inspection_policy': {'max_source_chars': 1000}}}


def test_scheme_preserves_partial_warning_when_no_overview_points_fit():
    original = package()
    original['coverage']['inspection_policy']['max_source_chars'] = 1
    before = deepcopy(original)
    overviews = [{'text': 'A supported interpretation.', 'sources': [
        {'id': 'overview:source', 'label': 'Original discussion', 'text': 'Exact original source.'}]}]
    result = scheme._with_conversation_overviews(original, overviews, ['Batch counts.', PARTIAL], 10000)
    assert result['coverage']['partial'] is True
    assert PARTIAL in result['coverage']['limitations']
    assert result['coverage']['conversation_overview_points'] == 0
    assert result['conversation_overviews'] == []
    assert result['sources'] == original['sources']
    assert original == before


def test_scheme_keeps_partial_warning_with_fitting_overviews():
    original = package()
    overviews = [{'text': 'A supported interpretation.', 'sources': [
        {'id': 'overview:source', 'label': 'Original discussion', 'text': 'Exact original source.'}]}]
    result = scheme._with_conversation_overviews(original, overviews, ['Batch counts.', PARTIAL], 10000)
    assert result['conversation_overviews'][0]['source_ids'] == ['overview:source']
    assert result['coverage']['partial'] is True
    assert PARTIAL in result['coverage']['limitations']


def test_scheme_without_overviews_or_omission_warning_preserves_existing_package():
    original = package()
    assert scheme._with_conversation_overviews(original, [], ['Batch counts.'], 10000) is original


def test_economy_knowledge_retains_archive_diagnostics_and_captured_worker_scope(monkeypatch):
    source = {'id': 'discussion:one', 'conversation_id': 'discussion', 'source_key': 'discussion',
              'role': 'user', 'text': 'A proposal.', 'revision': 'source-hash'}
    bundle = {'project': {'id': 1}, 'sources': [source],
              'manifest': {'fingerprint': 'sources', 'entries': []},
              'coverage': {'limitations': [], 'processed': [], 'unavailable': [],
                           'unprocessed': [], 'excluded': []}}
    diagnostics = {'archive_deferred_batches': 1, 'archive_recovered_batches': 1,
                   'archive_omissions': [{'phase': 'conversation_overviews_v1', 'points': 1}]}

    def overviews(*args, **kwargs):
        assert archive._archive_run.get()['workers'] == 3
        return [{'text': 'An interpretation.', 'sources': [source]}], ['Batch counts.', PARTIAL]

    monkeypatch.setattr(archive, 'conversation_overviews', overviews)
    monkeypatch.setattr(archive, 'archive_diagnostics', lambda: deepcopy(diagnostics))
    monkeypatch.setattr(knowledge, '_legacy_extraction_configurations', lambda *args: [])
    monkeypatch.setattr(knowledge, '_extract_windows', lambda *args: ([], 0, 0, 3, []))
    before = deepcopy(bundle)
    with use_knowledge_profile(get_generation_profile('economy'), workers=3):
        result = knowledge.build_candidate(None, 1, SimpleNamespace(), bundle)
    assert archive._archive_run.get() is None
    assert all(result['diagnostics'][key] == value for key, value in diagnostics.items())
    assert result['diagnostics']['extraction_workers'] == 3
    assert PARTIAL in result['coverage']['limitations']
    assert bundle == before
