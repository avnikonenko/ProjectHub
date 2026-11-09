"""Persist starred TODOs without changing existing tasks."""
import sqlalchemy as sa
from alembic import op

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tasks', sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('tasks', 'pinned')
