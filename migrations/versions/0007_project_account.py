"""Remember the Codex account profile selected for a project."""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('projects', sa.Column('codex_account', sa.String(200), nullable=True))
    op.add_column('codex_sessions', sa.Column('codex_account', sa.String(200), nullable=True))


def downgrade():
    raise RuntimeError('Restore a backup instead of removing project account settings.')
