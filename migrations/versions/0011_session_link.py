"""Preserve session data when a thread is unlinked from a project."""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('codex_sessions', sa.Column('is_linked', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column('codex_sessions', 'is_linked')
