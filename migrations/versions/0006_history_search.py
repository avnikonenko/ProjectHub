"""Index saved conversation text separately from generated summaries."""
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE TABLE codex_history_index (project_id INTEGER PRIMARY KEY REFERENCES projects(id), '
               'indexed_at VARCHAR NOT NULL, indexed_turns INTEGER NOT NULL)')
    op.execute('CREATE TABLE codex_history_turns (id INTEGER PRIMARY KEY, '
               'project_id INTEGER NOT NULL REFERENCES projects(id), '
               'session_id INTEGER NOT NULL REFERENCES codex_sessions(id), '
               'session_title TEXT NOT NULL, turn_id TEXT NOT NULL, status TEXT NOT NULL, '
               'messages TEXT NOT NULL, content TEXT NOT NULL, UNIQUE(project_id,session_id,turn_id))')
    op.execute('CREATE INDEX ix_codex_history_turns_project ON codex_history_turns(project_id)')
    op.execute("CREATE VIRTUAL TABLE codex_history_fts USING fts5(content, content='codex_history_turns', content_rowid='id')")
    op.execute('CREATE TRIGGER codex_history_insert AFTER INSERT ON codex_history_turns BEGIN '
               'INSERT INTO codex_history_fts(rowid,content) VALUES(new.id,new.content); END')
    op.execute("CREATE TRIGGER codex_history_delete AFTER DELETE ON codex_history_turns BEGIN "
               "INSERT INTO codex_history_fts(codex_history_fts,rowid,content) VALUES('delete',old.id,old.content); END")
    op.execute("CREATE TRIGGER codex_history_update AFTER UPDATE ON codex_history_turns BEGIN "
               "INSERT INTO codex_history_fts(codex_history_fts,rowid,content) VALUES('delete',old.id,old.content); "
               'INSERT INTO codex_history_fts(rowid,content) VALUES(new.id,new.content); END')


def downgrade():
    raise RuntimeError('Destructive downgrades are intentionally unsupported; restore a backup instead')
