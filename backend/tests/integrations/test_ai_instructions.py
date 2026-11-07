import pytest

from backend.app.integrations.codex import ai_instructions
from backend.app.integrations.codex.protocol import CodexError


def test_policy_reloads_and_missing_or_empty_files_fail_explicitly(tmp_path, monkeypatch):
    path = tmp_path / 'AGENTS_AI_agent.md'
    monkeypatch.setattr(ai_instructions, 'INSTRUCTIONS_PATH', path)
    with pytest.raises(CodexError, match='Could not read'):
        ai_instructions.ai_instructions()
    path.write_text('Be concise.', encoding='utf-8')
    assert ai_instructions.ai_instructions() == 'Be concise.'
    path.write_text('Provide detail when requested.', encoding='utf-8')
    assert ai_instructions.ai_instructions() == 'Provide detail when requested.'
    path.write_text('', encoding='utf-8')
    with pytest.raises(CodexError, match='between 1 and'):
        ai_instructions.ai_instructions()
