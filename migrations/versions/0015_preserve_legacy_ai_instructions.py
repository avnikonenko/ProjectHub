"""Distinguish legacy additions from editable replacement instructions."""
import sqlalchemy as sa
from alembic import op

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('project_ai_instructions', sa.Column(
        'replaces_shared', sa.Boolean(), nullable=False, server_default=sa.false(),
    ))


def downgrade():
    op.drop_column('project_ai_instructions', 'replaces_shared')
