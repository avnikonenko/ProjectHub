"""Save one separately generated focused scheme per project."""
from alembic import op
import sqlalchemy as sa

revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'project_focused_schemes',
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('snapshot', sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table('project_focused_schemes')
