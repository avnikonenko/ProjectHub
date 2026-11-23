"""Keep deleted IDs retired when old links and tabs can still reference them."""
from alembic import op
import sqlalchemy as sa

revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None

_COLUMNS = {
    'notes': 'id, project_id, title, content, kind, reference, created_at, updated_at, conversation',
    'conversation_import_sources': 'id, project_id, note_id, filename, reference, content_hash, payload, created_at',
    'generation_revisions': 'id, project_id, kind, generated_at, snapshot',
    'active_generation_revisions': 'project_id, kind, revision_id',
    'tasks': 'id, project_id, title, description, priority, status, question_id, decision_id, session_id, '
             'created_at, updated_at, pinned',
    'codex_events': 'id, project_id, session_id, method, payload, created_at',
}


def upgrade():
    connection = op.get_bind()
    # SQLite can run DDL in autocommit despite SQLAlchemy's logical transaction.
    # Keep table rebuilds and the Alembic version update atomic.
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN IMMEDIATE')
    names = tuple(_COLUMNS)
    placeholders = ', '.join('?' for _ in names)
    schema = connection.exec_driver_sql(
        f'SELECT sql FROM sqlite_schema WHERE tbl_name IN ({placeholders}) '
        "AND type IN ('index', 'trigger') AND sql IS NOT NULL ORDER BY type, name", names
    ).scalars().all()
    previous_sequences = {}
    if connection.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE name = 'sqlite_sequence'").scalar():
        previous_sequences = dict(connection.exec_driver_sql(
            f'SELECT name, seq FROM sqlite_sequence WHERE name IN ({placeholders})', names
        ).all())

    op.create_table(
        '_stable_notes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('reference', sa.Text(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        sa.Column('conversation', sa.JSON(), nullable=True),
        sqlite_autoincrement=True,
    )
    op.create_table(
        '_stable_conversation_import_sources',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('note_id', sa.Integer(), sa.ForeignKey('_stable_notes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('reference', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('payload', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_table(
        '_stable_generation_revisions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('generated_at', sa.String(), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_table(
        '_stable_active_generation_revisions',
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('kind', sa.String(10), primary_key=True),
        sa.Column('revision_id', sa.Integer(),
                  sa.ForeignKey('_stable_generation_revisions.id', ondelete='CASCADE'), nullable=False),
    )
    op.create_table(
        '_stable_tasks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('priority', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('question_id', sa.Integer(), sa.ForeignKey('questions.id')),
        sa.Column('decision_id', sa.Integer(), sa.ForeignKey('decisions.id')),
        sa.Column('session_id', sa.Integer(), sa.ForeignKey('codex_sessions.id')),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.false()),
        sqlite_autoincrement=True,
    )
    op.create_table(
        '_stable_codex_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('session_id', sa.Integer(), sa.ForeignKey('codex_sessions.id'), nullable=False),
        sa.Column('method', sa.String(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sqlite_autoincrement=True,
    )
    for table, columns in _COLUMNS.items():
        connection.exec_driver_sql(f'INSERT INTO _stable_{table} ({columns}) SELECT {columns} FROM {table}')

    # Each copied child points at its copied parent, so foreign keys can remain
    # enabled and SQLite renames their references along with the parent tables.
    for table in reversed(names):
        op.drop_table(table)
    for table in names:
        op.rename_table(f'_stable_{table}', table)
    for sql in schema:
        connection.exec_driver_sql(sql)

    # Before AUTOINCREMENT, note and task IDs may survive only in the audit
    # trail. Also retain prior sequence values across downgrade/re-upgrade.
    for table, identifier in connection.exec_driver_sql(
        "SELECT entity_type, max(entity_id) FROM audit_events WHERE entity_type IN ('notes', 'tasks') GROUP BY entity_type"
    ):
        previous_sequences[table] = max(identifier, previous_sequences.get(table, 0))
    for table, identifier in previous_sequences.items():
        result = connection.exec_driver_sql(
            'UPDATE sqlite_sequence SET seq = max(seq, ?) WHERE name = ?', (identifier, table)
        )
        if result.rowcount == 0:
            connection.exec_driver_sql('INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)', (table, identifier))


def downgrade():
    # Earlier revisions can use these tables. Keep retired IDs retired.
    pass
