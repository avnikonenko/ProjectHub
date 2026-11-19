"""Immutable AI generation trade-offs; evidence validation never varies."""
from dataclasses import asdict, dataclass
import os


@dataclass(frozen=True)
class GenerationProfile:
    id: str
    label: str
    description: str
    window_chars: int
    source_budget_chars: int | None
    default_effort: str | None
    experimental: bool


_PROFILES = (
    GenerationProfile(
        'quality', 'Thorough',
        'Tested default: smaller context-preserving batches and full available source coverage. '
        'Uses your selected model and reasoning effort.',
        14_000, None, None, False,
    ),
    GenerationProfile(
        'balanced', 'Balanced',
        'Experimental: gives long and short conversations a turn within the same text budget. '
        'Uses your selected model and reasoning effort.',
        18_000, None, None, True,
    ),
    GenerationProfile(
        'economy', 'Quick',
        'Experimental: reuses saved conversation overviews and limits original-message checks in Knowledge. '
        'Defaults to low reasoning when supported; explicit model and effort choices take precedence.',
        24_000, 60_000, 'low', True,
    ),
)


def get_generation_profile(name=None):
    """Capture one profile for a run; later settings changes do not mutate it."""
    name = os.environ.get('PROJECTHUB_GENERATION_PROFILE', 'quality') if name is None else name
    if not isinstance(name, str):
        raise ValueError('Generation profile must be quality, balanced or economy.')
    for profile in _PROFILES:
        if profile.id == name:
            return profile
    raise ValueError('Generation profile must be quality, balanced or economy.')


def list_generation_profiles():
    return [asdict(profile) for profile in _PROFILES]
