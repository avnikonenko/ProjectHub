"""Store Project Pulse on the existing brief row and in brief revisions."""
import sqlalchemy as sa
from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('project_briefs', sa.Column('project_state', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('project_briefs', 'project_state')
