"""Upgrade an existing 0019 database; never touches the user's data directory."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, ProjectBrief, ProjectKnowledge


def test_existing_brief_survives_additive_knowledge_migration(tmp_path):
    path = tmp_path / 'projecthub.sqlite3'
    engine = create_engine('sqlite:///' + str(path))
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).resolve().parents[3] / 'migrations'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, '0019')
    with Session(engine) as db:
        db.add(Project(name='Existing project')); db.flush()
        db.add(ProjectBrief(project_id=1, generated_at='before', sections={'overview': []}, sources=[], coverage=[]))
        db.commit()
    engine.dispose()
    updated, _ = initialize(tmp_path)
    with Session(updated) as db:
        assert db.get(Project, 1).name == 'Existing project'
        assert db.get(ProjectBrief, 1).generated_at == 'before'
        assert db.get(ProjectBrief, 1).sections == {'overview': []}
        assert db.get(ProjectKnowledge, 1) is None
    assert 'project_knowledge' in inspect(updated).get_table_names()
    updated.dispose()
