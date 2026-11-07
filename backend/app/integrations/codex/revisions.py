"""Append-only snapshots of successful project generations."""
from sqlalchemy import select

from ...models import GenerationRevision


def save_revision(db, project_id, kind, snapshot):
    db.add(GenerationRevision(project_id=project_id, kind=kind,
                              generated_at=snapshot['generated_at'], snapshot=snapshot))


def list_revisions(db, project_id, kind, offset):
    rows = list(db.scalars(select(GenerationRevision).where(
        GenerationRevision.project_id == project_id, GenerationRevision.kind == kind,
    ).order_by(GenerationRevision.id.desc()).offset(offset).limit(11)))
    return {
        'results': [{'id': row.id, 'generated_at': row.generated_at,
                     'generation': row.snapshot.get('generation')} for row in rows[:10]],
        'next_offset': offset + 10 if len(rows) > 10 else None,
    }
