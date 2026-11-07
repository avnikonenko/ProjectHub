"""Allow local automatic synchronization to be paused per saved session."""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('codex_sessions', sa.Column('auto_sync', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column('codex_sessions', 'auto_sync')
