"""Add optional session topic and Codex-reported activity metadata."""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('codex_sessions', sa.Column('topic', sa.String(80), nullable=False, server_default=''))
    op.add_column('codex_sessions', sa.Column('codex_activity_at', sa.String(), nullable=True))


def downgrade():
    op.drop_column('codex_sessions', 'codex_activity_at')
    op.drop_column('codex_sessions', 'topic')
