"""Reserve removed project IDs and persist unfinished upload cleanup."""
from alembic import op
import sqlalchemy as sa

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'removed_projects',
        sa.Column('project_id', sa.Integer(), primary_key=True),
        sa.Column('pending_uploads', sa.JSON(), nullable=False),
    )


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM removed_projects LIMIT 1')).first() is not None:
        raise RuntimeError('Removed project identities must remain reserved. Restore a backup instead.')
    op.drop_table('removed_projects')
