"""Length diagnostics remain distinct from unmatched or ambiguous source evidence."""
import pytest

from backend.app.integrations.codex.knowledge import _source_excerpt
from backend.app.integrations.codex.protocol import CodexError


GENERIC = 'Knowledge evidence does not match the original speaker and excerpt.'


def test_exact_evidence_accepts_1600_characters_without_alteration():
    quote = 'x' * 1600
    assert _source_excerpt(quote, 'Before ' + quote + ' after') == quote


def test_exact_evidence_rejects_1601_characters_with_length_diagnostic():
    quote = 'x' * 1601
    with pytest.raises(CodexError, match='exceeds the 1600-character limit'):
        _source_excerpt(quote, quote)


def test_normalized_original_span_accepts_exact_limit_without_reconstruction():
    original = 'Start' + '\n' * 1592 + 'end'
    assert len(original) == 1600
    assert _source_excerpt('Start end', original) == original


def test_normalized_original_span_rejects_1601_with_length_diagnostic():
    original = 'Start' + '\n' * 1593 + 'end'
    assert len(original) == 1601
    with pytest.raises(CodexError, match='exceeds the 1600-character limit'):
        _source_excerpt('Start end', original)


def test_ambiguous_expansion_keeps_original_mismatch_diagnostic_before_length():
    original = 'Start' + '\n' * 1593 + 'end'
    with pytest.raises(CodexError) as error:
        _source_excerpt('Start end', original + '\nDifferent passage\nStart\tend')
    assert str(error.value) == GENERIC


@pytest.mark.parametrize(('quote', 'text'), [
    (None, 'Source text'),
    (123, 'Source text'),
    ('', 'Source text'),
    (' \t\n' * 600, 'Source text'),
    ('Not present', 'Source text'),
    ('Start end', 'Start\nend / Start\tend'),
    ('a a', 'a\na\na'),
])
def test_non_length_failures_keep_original_diagnostic(quote, text):
    with pytest.raises(CodexError) as error:
        _source_excerpt(quote, text)
    assert str(error.value) == GENERIC
