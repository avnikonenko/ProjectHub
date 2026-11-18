"""Record the settings used for the latest generation attempt."""
from alembic import op
import sqlalchemy as sa

revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('project_generation_attempts', sa.Column('settings', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('project_generation_attempts', 'settings')
