"""Append-only snapshots of successful project generations."""
from sqlalchemy import select

from ...models import GenerationRevision


def save_revision(db, project_id, kind, snapshot):
    db.add(GenerationRevision(project_id=project_id, kind=kind,
                              generated_at=snapshot['generated_at'], snapshot=snapshot))


def list_revisions(db, project_id, kind, offset):
    # Extract only the listing metadata in SQLite. Loading whole ORM entities
    # deserializes eleven potentially large snapshots just to return ten labels.
    rows = db.execute(select(
        GenerationRevision.id, GenerationRevision.generated_at,
        GenerationRevision.snapshot['generation'].label('generation'),
    ).where(
        GenerationRevision.project_id == project_id, GenerationRevision.kind == kind,
    ).order_by(GenerationRevision.id.desc()).offset(offset).limit(11)).mappings().all()
    return {
        'results': [{'id': row.id, 'generated_at': row.generated_at,
                     'generation': row.generation} for row in rows[:10]],
        'next_offset': offset + 10 if len(rows) > 10 else None,
    }
