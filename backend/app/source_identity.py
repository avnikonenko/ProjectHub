"""Recognize live notes whose numeric ID belonged to a deleted note."""

from sqlalchemy import select

from .models import AuditEvent, Note


def recycled_note_ids(db, project_id, note_id=None):
    deleted = select(AuditEvent.entity_id).where(
        AuditEvent.project_id == project_id, AuditEvent.entity_type == 'notes',
        AuditEvent.action == 'deleted',
    )
    notes = select(Note.id, Note.created_at).where(Note.project_id == project_id, Note.id.in_(deleted))
    if note_id is not None:
        notes = notes.where(Note.id == note_id)
    return {row.id: row.created_at for row in db.execute(notes)}


def matches_record_incarnation(db, record, created_at):
    if created_at is not None:
        return record.created_at == created_at
    return db.scalar(select(AuditEvent.id).where(
        AuditEvent.project_id == record.project_id, AuditEvent.entity_type == record.__tablename__,
        AuditEvent.entity_id == record.id, AuditEvent.action == 'deleted',
    ).limit(1)) is None
