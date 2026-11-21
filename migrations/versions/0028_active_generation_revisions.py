"""Record explicit selections from generated history."""
from alembic import op
import sqlalchemy as sa

revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('active_generation_revisions',
                    sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
                    sa.Column('kind', sa.String(10), primary_key=True),
                    sa.Column('revision_id', sa.Integer(), sa.ForeignKey('generation_revisions.id', ondelete='CASCADE'),
                              nullable=False))


def downgrade():
    op.drop_table('active_generation_revisions')
