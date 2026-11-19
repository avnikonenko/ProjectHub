"""Distinguish registered Codex and Claude Code profiles."""
from alembic import op
import sqlalchemy as sa

revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('codex_profiles', sa.Column('provider', sa.String(16), nullable=False, server_default='codex'))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT 1 FROM codex_profiles WHERE provider <> 'codex' LIMIT 1")):
        raise RuntimeError('Downgrading would lose Claude Code profile information.')
    op.drop_column('codex_profiles', 'provider')
