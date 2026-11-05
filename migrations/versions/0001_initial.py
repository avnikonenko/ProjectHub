"""Initial ProjectHub knowledge and Codex schema."""
from alembic import op
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Frozen DDL: subsequent ORM changes require a new migration revision.
    tables = {
        'projects': "id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL, description TEXT NOT NULL, goals TEXT NOT NULL, status VARCHAR NOT NULL, repository_path TEXT, git_remote TEXT NOT NULL, tags JSON NOT NULL, architecture_mermaid TEXT NOT NULL",
        'questions': "title TEXT NOT NULL, description TEXT NOT NULL, category VARCHAR NOT NULL, priority VARCHAR NOT NULL, status VARCHAR NOT NULL, accepted_answer_id INTEGER REFERENCES answers(id)",
        'answers': "question_id INTEGER NOT NULL REFERENCES questions(id), solution TEXT NOT NULL, explanation TEXT NOT NULL, advantages TEXT NOT NULL, limitations TEXT NOT NULL, evidence TEXT NOT NULL, source TEXT NOT NULL, status VARCHAR NOT NULL",
        'decisions': "title TEXT NOT NULL, description TEXT NOT NULL, rationale TEXT NOT NULL, alternatives TEXT NOT NULL, evidence TEXT NOT NULL, limitations TEXT NOT NULL, status VARCHAR NOT NULL, question_id INTEGER REFERENCES questions(id), answer_id INTEGER REFERENCES answers(id)",
        'tasks': "title TEXT NOT NULL, description TEXT NOT NULL, priority VARCHAR NOT NULL, status VARCHAR NOT NULL, question_id INTEGER REFERENCES questions(id), decision_id INTEGER REFERENCES decisions(id), session_id INTEGER REFERENCES codex_sessions(id)",
        'notes': "title TEXT NOT NULL, content TEXT NOT NULL, kind VARCHAR NOT NULL, reference TEXT NOT NULL",
        'codex_sessions': "thread_id VARCHAR NOT NULL UNIQUE, title TEXT NOT NULL, status VARCHAR NOT NULL, last_error TEXT NOT NULL",
        'context_packages': "session_id INTEGER REFERENCES codex_sessions(id), content TEXT NOT NULL, version VARCHAR NOT NULL",
        'deliveries': "session_id INTEGER NOT NULL REFERENCES codex_sessions(id), decision_id INTEGER REFERENCES decisions(id), context_id INTEGER REFERENCES context_packages(id), content TEXT NOT NULL, status VARCHAR NOT NULL, error TEXT NOT NULL, turn_id VARCHAR",
    }
    for table, columns in tables.items():
        scope = '' if table == 'projects' else 'id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), '
        op.execute(f'CREATE TABLE {table} ({scope}{columns}, created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL)')
        if table != 'projects': op.execute(f'CREATE INDEX ix_{table}_project_id ON {table}(project_id)')
    op.execute('CREATE TABLE audit_events (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), entity_type VARCHAR NOT NULL, entity_id INTEGER NOT NULL, action VARCHAR NOT NULL, before JSON, after JSON, created_at VARCHAR NOT NULL)')
    op.execute('CREATE INDEX ix_audit_events_project_id ON audit_events(project_id)')
    op.execute('CREATE TABLE codex_events (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), session_id INTEGER NOT NULL REFERENCES codex_sessions(id), method VARCHAR NOT NULL, payload JSON NOT NULL, created_at VARCHAR NOT NULL)')
    op.execute('CREATE INDEX ix_codex_events_project_id ON codex_events(project_id)')
    op.execute('CREATE INDEX ix_codex_events_session_id ON codex_events(session_id)')
    op.execute('CREATE INDEX ix_answers_question_id ON answers(question_id)')
    op.execute('CREATE VIRTUAL TABLE search_index USING fts5(project_id UNINDEXED, entity_type UNINDEXED, entity_id UNINDEXED, title, content)')
    for table, title, content in [
        ('projects', 'name', "description || ' ' || goals"),
        ('questions', 'title', 'description'),
        ('answers', 'solution', "explanation || ' ' || evidence"),
        ('decisions', 'title', "description || ' ' || rationale || ' ' || evidence"),
        ('tasks', 'title', 'description'),
        ('notes', 'title', 'content'),
    ]:
        project_id = 'id' if table == 'projects' else 'project_id'
        # Identifiers and expressions are fixed migration constants, never user input.
        new_content = content.replace('description', 'new.description').replace('goals', 'new.goals').replace('explanation', 'new.explanation').replace('evidence', 'new.evidence').replace('rationale', 'new.rationale')
        if content == 'content': new_content = 'new.content'
        op.execute(f"CREATE TRIGGER {table}_search_insert AFTER INSERT ON {table} BEGIN INSERT INTO search_index(project_id,entity_type,entity_id,title,content) VALUES(new.{project_id},'{table}',new.id,new.{title},{new_content}); END")
        op.execute(f"CREATE TRIGGER {table}_search_update AFTER UPDATE ON {table} BEGIN DELETE FROM search_index WHERE entity_type='{table}' AND entity_id=old.id; INSERT INTO search_index(project_id,entity_type,entity_id,title,content) VALUES(new.{project_id},'{table}',new.id,new.{title},{new_content}); END")
        op.execute(f"CREATE TRIGGER {table}_search_delete AFTER DELETE ON {table} BEGIN DELETE FROM search_index WHERE entity_type='{table}' AND entity_id=old.id; END")


def downgrade():
    raise RuntimeError('Destructive downgrades are intentionally unsupported; restore a backup instead')
