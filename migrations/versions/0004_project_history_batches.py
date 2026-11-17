"""Cache history summaries and store an optional project map."""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('project_briefs', sa.Column('project_map', sa.JSON(), nullable=True))
    op.create_table(
        'project_history_batches',
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id'), primary_key=True),
        sa.Column('fingerprint', sa.String(64), primary_key=True),
        sa.Column('evidence', sa.JSON(), nullable=False),
    )


def downgrade():
    raise RuntimeError('Destructive downgrades are intentionally unsupported; restore a backup instead')
