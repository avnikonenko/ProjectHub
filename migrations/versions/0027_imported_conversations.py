"""Attach validated conversation metadata to imported documents."""
from alembic import op
import sqlalchemy as sa

revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('notes', sa.Column('conversation', sa.JSON(), nullable=True))
    op.execute("CREATE INDEX ix_notes_project_conversation_fingerprint ON notes "
               "(project_id, json_extract(conversation, '$.fingerprint')) WHERE conversation IS NOT NULL")
    op.create_table('conversation_import_sources',
                    sa.Column('id', sa.Integer(), primary_key=True),
                    sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
                    sa.Column('note_id', sa.Integer(), sa.ForeignKey('notes.id', ondelete='CASCADE'), nullable=False),
                    sa.Column('filename', sa.String(255), nullable=False),
                    sa.Column('reference', sa.Text(), nullable=False),
                    sa.Column('content_hash', sa.String(64), nullable=False),
                    sa.Column('payload', sa.LargeBinary(), nullable=False),
                    sa.Column('created_at', sa.String(), nullable=False))
    op.create_index('ix_conversation_import_sources_note', 'conversation_import_sources', ['project_id', 'note_id'])


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT 1 FROM conversation_import_sources LIMIT 1')):
        raise RuntimeError('Downgrading would remove preserved conversation sources.')
    op.drop_table('conversation_import_sources')
    op.drop_index('ix_notes_project_conversation_fingerprint', table_name='notes')
    op.drop_column('notes', 'conversation')
