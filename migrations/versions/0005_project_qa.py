"""Save the generated Q&A separately from the project brief."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'project_qa',
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id'), primary_key=True),
        sa.Column('generated_at', sa.String(), nullable=False),
        sa.Column('items', sa.JSON(), nullable=False),
        sa.Column('sources', sa.JSON(), nullable=False),
    )


def downgrade():
    raise RuntimeError('Destructive downgrades are intentionally unsupported; restore a backup instead')
