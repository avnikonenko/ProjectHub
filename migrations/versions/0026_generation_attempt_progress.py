"""Retain generation attempt progress across restarts."""
from alembic import op
import sqlalchemy as sa

revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('project_generation_attempts', sa.Column('progress', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('project_generation_attempts', 'progress')
