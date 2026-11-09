"""Add opt-in evidence-backed knowledge snapshots without modifying existing reports."""
import sqlalchemy as sa
from alembic import op

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('project_knowledge',
                    sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
                    sa.Column('snapshot', sa.JSON(), nullable=False))


def downgrade():
    op.drop_table('project_knowledge')
