import pytest

from backend.app.integrations.codex import ai_instructions
from backend.app.integrations.codex.protocol import CodexError


def test_policy_reloads_and_missing_or_empty_files_fail_explicitly(tmp_path, monkeypatch):
    path = tmp_path / 'AGENTS_AI_agent.md'
    monkeypatch.setattr(ai_instructions, 'INSTRUCTIONS_PATH', path)
    with pytest.raises(CodexError, match='Could not read'):
        ai_instructions.ai_instructions()
    path.write_text('Be concise.', encoding='utf-8')
    assert ai_instructions.ai_instructions().startswith('Be concise.\n\n')
    path.write_text('Provide detail when requested.', encoding='utf-8')
    assert ai_instructions.ai_instructions().startswith('Provide detail when requested.\n\n')
    with ai_instructions.use_project_instructions(''):
        result = ai_instructions.ai_instructions()
        assert 'Provide detail when requested.' not in result
        assert 'Remain read-only' in result
    path.write_text('', encoding='utf-8')
    with pytest.raises(CodexError, match='between 1 and'):
        ai_instructions.ai_instructions()
