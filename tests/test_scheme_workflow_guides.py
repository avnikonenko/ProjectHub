import pytest

from backend.app.integrations.codex.scheme_workflow_guides import is_workflow_alternative


@pytest.mark.parametrize(
    'paragraph',
    [
        'To start from docked or X-ray seed poses, add\n'
        '`--seed-poses poses.sdf --input-seed-smiles seeds.smi`. '
        'Providing poses automatically reuses them.',
        'Missing, blank, invalid, or non-finite scores fail initialization; no rescoring is\n'
        'performed. To dock seeds normally, supply only `--input-seed-smiles seeds.smi` '
        'and omit `--seed-poses`.',
        'When fresh input tables are supplied, the worker skips cached input.',
        'If a saved dataset exists, the job does not generate a new one.',
        'To start with raw audio, omit `--cached-transcript`.',
    ],
)
def test_explicit_input_choice_with_workflow_effect(paragraph):
    assert is_workflow_alternative(paragraph)


@pytest.mark.parametrize(
    'paragraph',
    [
        'Optionally add `--verbose` for more detail.',
        'Use version 1.3 or 1.4. Either is supported.',
        'If using a legacy version, install the compatibility package.',
        'To sign in, provide the credentials file. When provided, it is reused.',
        'When reusing authentication tokens, omit `--interactive`.',
        'Reruns may reuse generated results.',
        'The tool performs default validation.',
        'The boolean value supplies the flag. Environment interpolation is not performed.',
    ],
)
def test_incidental_options_are_not_workflow_alternatives(paragraph):
    assert not is_workflow_alternative(paragraph)
