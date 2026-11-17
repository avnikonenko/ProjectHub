"""Persist pinned projects without changing existing project data."""
import sqlalchemy as sa
from alembic import op

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('projects', sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('projects', 'pinned')
