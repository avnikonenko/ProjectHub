import pytest

from backend.app.integrations.codex import ai_instructions
from backend.app.integrations.codex.protocol import CodexError


def test_policy_reloads_and_missing_or_empty_files_fail_explicitly(tmp_path, monkeypatch):
    path = tmp_path / 'AI_RECOMMENDATIONS.md'
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


def test_legacy_instruction_rows_keep_shared_recommendations_after_upgrade(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from backend.app.models import ProjectAIInstructions

    path = tmp_path / 'AI_RECOMMENDATIONS.md'
    path.write_text('Shared recommendation')
    monkeypatch.setattr(ai_instructions, 'INSTRUCTIONS_PATH', path)
    engine = create_engine(f'sqlite:///{tmp_path / "project.sqlite"}')
    cfg = Config()
    cfg.set_main_option('script_location', 'migrations')
    with engine.begin() as connection:
        cfg.attributes['connection'] = connection
        command.upgrade(cfg, '0014')
        for project_id, instructions in ((1, ''), (2, 'Legacy guidance')):
            connection.exec_driver_sql(
                "INSERT INTO projects(id,name,description,goals,status,git_remote,tags,architecture_mermaid,created_at,updated_at) "
                "VALUES(?,'Example','','','planning','','[]','','now','now')", (project_id,))
            connection.exec_driver_sql('INSERT INTO project_ai_instructions VALUES (?,?)', (project_id, instructions))
        command.upgrade(cfg, 'head')

    with Session(engine) as db:
        assert ai_instructions.read_project_instructions(db, 1) is None
        assert ai_instructions.read_project_instructions(db, 2) == 'Shared recommendation\n\nLegacy guidance'
        db.get(ProjectAIInstructions, 2).replaces_shared = True
        db.get(ProjectAIInstructions, 2).instructions = ''
        db.commit()
        assert ai_instructions.read_project_instructions(db, 2) == ''
    engine.dispose()
