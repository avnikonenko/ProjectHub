"""Shared authoring instructions; loaded afresh for each generation request."""
from pathlib import Path

from .protocol import CodexError

INSTRUCTIONS_PATH = Path(__file__).resolve().parents[4] / 'AGENTS_AI_agent.md'


def ai_instructions():
    try:
        text = INSTRUCTIONS_PATH.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as exc:
        raise CodexError('Could not read AGENTS_AI_agent.md. Restore the shared AI instructions and retry.') from exc
    if not text.strip() or len(text) > 20_000:
        raise CodexError('AGENTS_AI_agent.md must contain between 1 and 20,000 characters.')
    return text
