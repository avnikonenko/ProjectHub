"""Keep latest generation outcomes independently of saved results."""
import sqlalchemy as sa
from alembic import op

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('project_generation_attempts',
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('kind', sa.String(32), primary_key=True),
        sa.Column('started_at', sa.String(80), nullable=False),
        sa.Column('finished_at', sa.String(80), nullable=True),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('saves_result', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_table('project_generation_attempts')
