"""Store user-registered Codex directories without credentials."""
import sqlalchemy as sa
from alembic import op

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'codex_profiles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(80), nullable=False),
        sa.Column('directory', sa.Text(), nullable=False, unique=True),
    )


def downgrade():
    op.drop_table('codex_profiles')
