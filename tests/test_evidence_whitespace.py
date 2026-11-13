"""Whitespace selectors resolve to real source spans, never reconstructed quotes."""
import json

import pytest

from backend.app.integrations.codex.knowledge import _source_excerpt, validate_extraction
from backend.app.integrations.codex.protocol import CodexError


def extraction(text, quote, *, role='assistant', identifier='original'):
    source = {'id': 'original', 'role': 'assistant', 'text': text, 'revision': 'r1',
              'conversation_id': 'conversation', 'order': 1, 'source_key': 'conversation'}
    raw = json.dumps({'items': [{'statement': 'Consider the stated backend.', 'category': 'PROPOSAL',
        'design_status': 'PROPOSED', 'scope': 'backend', 'rationale': '', 'alternatives': '',
        'limitations': '', 'agreement_basis': 'none', 'revises_statement': '', 'conflicting_statements': [],
        'evidence': [{'source_id': identifier, 'role': role, 'excerpt': quote}]}]})
    return validate_extraction(raw, {'sources': [source], 'primary_ids': ['original']}, 1, 'v1')


@pytest.mark.parametrize(('text', 'quote', 'expected'), [
    ('Before.\n\nRepair only selected gaps.\n\n\nCheck the result.\nAfter.',
     'Repair only selected gaps.\n\nCheck the result.',
     'Repair only selected gaps.\n\n\nCheck the result.'),
    ('Before.\r\nMODELLER is optional.\r\n\r\nReview each gap.\r\nAfter.',
     'MODELLER is optional.\n\nReview each gap.',
     'MODELLER is optional.\r\n\r\nReview each gap.'),
    ('Use\tselected   tools.', 'Use selected tools.', 'Use\tselected   tools.'),
    ('PDBFixer + RDKit (optional).', 'PDBFixer + RDKit (optional).', 'PDBFixer + RDKit (optional).'),
])
def test_unique_whitespace_selector_stores_exact_original_span(text, quote, expected):
    item = extraction(text, quote)[0]
    assert item['evidence'][0]['excerpt'] == expected
    assert expected in text
    assert item['evidence'][0]['revision'] == 'r1'
    assert item['design_status'] == 'PROPOSED'


@pytest.mark.parametrize('quote', [
    'Use different tools.', 'use selected tools.', 'Use selected tools!',
    'Use selectedtool s.', 'Use selected  tools. Extra words.',
])
def test_content_changes_are_rejected(quote):
    with pytest.raises(CodexError, match='speaker and excerpt'):
        extraction('Use selected\n\ntools.', quote)


@pytest.mark.parametrize(('text', 'quote'), [
    ('Use\tselected tools. Also: Use\nselected tools.', 'Use  selected tools.'),
    ('a a a', 'a\na'),
])
def test_multiple_normalized_occurrences_including_overlap_are_rejected(text, quote):
    with pytest.raises(CodexError, match='speaker and excerpt'):
        extraction(text, quote)


def test_exact_existing_quote_is_unchanged_even_with_multiple_occurrences():
    quote = 'Keep\nthis exact.'
    text = quote + '\nOther paragraph.\n' + quote
    assert extraction(text, quote)[0]['evidence'][0]['excerpt'] == quote


@pytest.mark.parametrize(('role', 'identifier', 'message'), [
    ('user', 'original', 'speaker and excerpt'),
    ('assistant', 'foreign', 'foreign evidence'),
])
def test_source_and_speaker_must_match_before_span_resolution(role, identifier, message):
    with pytest.raises(CodexError, match=message):
        extraction('Use\nselected tools.', 'Use selected tools.', role=role, identifier=identifier)


def test_restored_source_span_must_remain_within_evidence_limit():
    text = 'Start' + '\n' * 1595 + 'end'
    assert len(text) > 1600
    with pytest.raises(CodexError, match='speaker and excerpt'):
        extraction(text, 'Start end')


@pytest.mark.parametrize('quote', ['', ' \n\t', 'x' * 1601, None])
def test_model_selector_must_be_nonempty_bounded_text(quote):
    with pytest.raises(CodexError, match='speaker and excerpt'):
        _source_excerpt(quote, 'x' * 2000)


def test_normalization_does_not_remove_or_insert_word_boundaries():
    with pytest.raises(CodexError, match='speaker and excerpt'):
        extraction('Use selectedtools.', 'Use selected tools.')
    with pytest.raises(CodexError, match='speaker and excerpt'):
        extraction('Use selected tools.', 'Use selectedtools.')


@pytest.mark.parametrize('restore', [False, True])
@pytest.mark.parametrize('duplicates', [False, True])
def test_cache_stores_literal_source_spans_compatible_with_exact_only_validators(tmp_path, monkeypatch, restore, duplicates):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from backend.app.db import initialize
    from backend.app.models import Project, ProjectHistoryBatch
    from backend.app.integrations.codex import knowledge

    original_text = 'Use\n\nselected tools.'
    model_quote = 'Use selected tools.' if restore else original_text
    ref = {'source_id': 'original', 'role': 'assistant', 'excerpt': model_quote}
    raw = json.dumps({'items': [{'statement': 'Consider the stated backend.', 'category': 'PROPOSAL',
        'design_status': 'PROPOSED', 'scope': 'backend', 'rationale': '', 'alternatives': '',
        'limitations': '', 'agreement_basis': 'none', 'revises_statement': '', 'conflicting_statements': [],
        'evidence': [dict(ref) for _ in range(2 if duplicates else 1)]}]}, indent=2)
    window = {'sources': [{'id': 'original', 'role': 'assistant', 'text': original_text,
                          'revision': 'r1', 'conversation_id': 'one', 'order': 1, 'source_key': 'one'}],
              'primary_ids': ['original'], 'context_ids': []}
    class Manager:
        calls = 0
        def generate_brief(self, *_):
            self.calls += 1
            assert self.calls == 1, 'Cache replay invoked the model'
            return raw
    engine, _ = initialize(tmp_path)
    try:
        with Session(engine) as db:
            db.add(Project(name='Quotes')); db.commit()
        manager = Manager(); config = knowledge.extraction_configuration(manager)
        values, cached = knowledge.extract_batch(engine, 1, manager, window, config)
        assert not cached
        with Session(engine) as db:
            envelope = db.scalars(select(ProjectHistoryBatch)).one().evidence[0]
        canonical = json.loads(envelope['raw'])
        assert set(canonical['items'][0]) == set(knowledge.OUTPUT_SCHEMA['properties']['items']['items']['properties'])
        assert len(canonical['items'][0]['evidence']) == (2 if duplicates else 1)
        assert all(set(ref) == {'source_id', 'role', 'excerpt'} and ref['excerpt'] == original_text
                   for ref in canonical['items'][0]['evidence'])
        if restore:
            assert envelope['selector_adjustments'] == [
                {'item': 0, 'evidence': i, 'original_excerpt': model_quote}
                for i in range(2 if duplicates else 1)]
        else:
            assert envelope['raw'] == raw
            assert 'selector_adjustments' not in envelope
        def exact_only(quote, text):
            assert quote in text, 'Stored response requires the new whitespace resolver'
            return quote
        monkeypatch.setattr(knowledge, '_source_excerpt', exact_only)
        replay, cached = knowledge.extract_batch(engine, 1, manager, window, config)
        assert cached and replay == values and manager.calls == 1
    finally:
        engine.dispose()
