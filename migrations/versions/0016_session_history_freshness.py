"""Remember each session's successful indexed activity marker."""
import sqlalchemy as sa
from alembic import op

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('codex_sessions', sa.Column('history_indexed_at', sa.String(), nullable=True))
    op.add_column('codex_sessions', sa.Column('history_indexed_activity', sa.String(), nullable=True))


def downgrade():
    op.drop_column('codex_sessions', 'history_indexed_activity')
    op.drop_column('codex_sessions', 'history_indexed_at')
