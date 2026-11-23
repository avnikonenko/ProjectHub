from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.app.models import Project


def test_fresh_app_factories_can_migrate_separate_data_directories_concurrently(tmp_path):
    start = Barrier(2)

    def build(name):
        start.wait(timeout=10)
        app = create_app(tmp_path / name)
        try:
            with Session(app.state.engine) as db:
                db.add(Project(name=name))
                db.commit()
                return db.scalars(select(Project.name)).all()
        finally:
            app.state.engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(build, ('first', 'second'))) == [['first'], ['second']]
