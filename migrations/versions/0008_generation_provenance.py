"""Remember the generation settings of saved brief and Q&A snapshots."""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    for table in ('project_briefs', 'project_qa'):
        op.add_column(table, sa.Column('generation', sa.JSON(), nullable=True))


def downgrade():
    raise RuntimeError('Restore a backup instead of removing generation metadata.')
