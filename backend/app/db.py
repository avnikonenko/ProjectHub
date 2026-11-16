import os
from pathlib import Path
from sqlalchemy import URL, create_engine, event
from sqlalchemy.orm import Session
from fastapi import Request
from alembic import command
from alembic.config import Config


def default_data_dir():
    return Path(os.environ.get('PROJECTHUB_DATA_DIR', Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'projecthub'))


def initialize(data_dir):
    data_dir = Path(data_dir).expanduser().absolute()
    if any(p.is_symlink() for p in (data_dir, *data_dir.parents)):
        raise ValueError('Application data directory must not be a symlink')
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    db_path = data_dir / 'projecthub.sqlite3'
    if db_path.is_symlink():
        raise ValueError('Database must not be a symlink')
    engine = create_engine(URL.create('sqlite', database=str(db_path)),
                           connect_args={'check_same_thread': False, 'timeout': 15})
    @event.listens_for(engine, 'connect')
    def setup(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=15000')
    cfg = Config()
    cfg.set_main_option('script_location', str(Path(__file__).resolve().parents[2] / 'migrations'))
    with engine.begin() as connection:
        cfg.attributes['connection'] = connection
        command.upgrade(cfg, 'head')
    db_path.chmod(0o600)
    return engine, data_dir


def get_db(request: Request):
    with Session(request.app.state.engine) as db:
        try:
            yield db
        except Exception:
            db.rollback()
            raise
