"""Add an optional next step to decisions."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('decisions', sa.Column('next_step', sa.Text(), nullable=False, server_default=''))


def downgrade():
    raise RuntimeError('Destructive downgrades are intentionally unsupported; restore a backup instead')
