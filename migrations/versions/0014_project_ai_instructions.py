"""Store per-project AI authoring preferences."""
import sqlalchemy as sa
from alembic import op

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'project_ai_instructions',
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('instructions', sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table('project_ai_instructions')
