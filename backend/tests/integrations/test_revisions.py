import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, GenerationRevision
from backend.app.integrations.codex.revisions import save_revision, list_revisions
from backend.app.integrations.codex.router import revision_snapshot


def test_revisions_page_and_project_scope(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(name='One'), Project(name='Two')])
        db.flush()
        for number in range(13):
            save_revision(db, 1, 'qa', {'generated_at': str(number), 'items': [], 'sources': []})
        save_revision(db, 2, 'qa', {'generated_at': 'foreign', 'items': [], 'sources': []})
        db.commit()
        first = list_revisions(db, 1, 'qa', 0)
        second = list_revisions(db, 1, 'qa', first['next_offset'])
        assert len(first['results']) == 10 and len(second['results']) == 3
        assert second['next_offset'] is None
        rid = first['results'][0]['id']
        assert revision_snapshot(1, rid, db)['snapshot']['generated_at'] == '12'
        with pytest.raises(HTTPException) as error:
            revision_snapshot(2, rid, db)
        assert error.value.status_code == 404
    engine.dispose()


def test_migration_seeds_existing_qa(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'projecthub.sqlite3'))
    cfg = Config()
    cfg.set_main_option('script_location', 'migrations')
    with engine.begin() as connection:
        cfg.attributes['connection'] = connection
        command.upgrade(cfg, '0008')
        connection.exec_driver_sql(
            "INSERT INTO projects(id,name,description,goals,status,git_remote,tags,architecture_mermaid,created_at,updated_at) "
            "VALUES(1,'Existing','','','planning','','[]','','now','now')")
        connection.exec_driver_sql(
            "INSERT INTO project_qa(project_id,generated_at,items,sources,generation) VALUES(1,'before','[]','[]',NULL)")
        command.upgrade(cfg, 'head')
    with Session(engine) as db:
        row = db.scalar(select(GenerationRevision))
        assert row.kind == 'qa' and row.project_id == 1
        assert row.snapshot == {'generated_at': 'before', 'items': [], 'sources': [], 'generation': None}
    engine.dispose()
