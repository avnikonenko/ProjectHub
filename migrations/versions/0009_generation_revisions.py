"""Preserve the existing latest snapshots and future generation history."""
from alembic import op
import sqlalchemy as sa
import json

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('generation_revisions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('generated_at', sa.String(), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False))
    op.create_index('ix_generation_revisions_project_id', 'generation_revisions', ['project_id'])
    connection = op.get_bind()
    # Seed existing snapshots without generating, modifying or guessing content.
    for kind, table in [('brief', 'project_briefs'), ('qa', 'project_qa')]:
        for row in connection.execute(sa.text(f'SELECT * FROM {table}')).mappings():
            snapshot = {key: value for key, value in row.items() if key != 'project_id'}
            for key in ('sections', 'sources', 'coverage', 'project_map', 'items', 'generation'):
                if key in snapshot and isinstance(snapshot[key], str):
                    snapshot[key] = json.loads(snapshot[key])
            connection.execute(sa.text(
                'INSERT INTO generation_revisions(project_id,kind,generated_at,snapshot) '
                'VALUES(:pid,:kind,:at,:snapshot)'),
                {'pid': row['project_id'], 'kind': kind, 'at': row['generated_at'],
                 'snapshot': json.dumps(snapshot, ensure_ascii=False)})


def downgrade():
    raise RuntimeError('Restore a backup instead of deleting generation history.')
