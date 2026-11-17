"""Store one current evidence-backed scheme per project; revisions reuse existing storage."""
import sqlalchemy as sa
from alembic import op

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('project_schemes',
                    sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
                    sa.Column('snapshot', sa.JSON(), nullable=False))


def downgrade():
    op.drop_table('project_schemes')
