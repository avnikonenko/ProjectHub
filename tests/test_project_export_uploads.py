"""Managed export attachments come from scoped, immutable import audit events."""
from io import BytesIO
from zipfile import ZipFile

from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import AuditEvent, Note, Project
from backend.app.project_export import _archive_uploads
from backend.app.services import audit


def _render(db, pid):
    output = BytesIO()
    with ZipFile(output, 'w') as archive:
        paths = _archive_uploads(archive, db, pid)
    with ZipFile(output) as archive:
        return paths, {path: archive.read(path) for path in archive.namelist()}


def test_archived_upload_uses_original_bytes_after_note_and_disk_changes(tmp_path):
    engine, directory = initialize(tmp_path)
    uploads = directory / 'uploads'
    uploads.mkdir(exist_ok=True)
    root = 'e' * 32 + '.md'
    missing = 'd' * 32 + '.txt'
    original = b'\xef\xbb\xbfCaf\xc3\xa9\r\nOriginal text\n'
    (uploads / root).write_bytes(b'Different bytes in the managed file')
    with Session(engine) as db:
        db.add(Project(id=1, name='Selected'))
        db.flush()
        first = Note(id=1, project_id=1, kind='document', title='Archive original',
                     reference=root, content=original.decode())
        second = Note(id=2, project_id=1, kind='document', title='Archive missing',
                      reference=missing, content='')
        db.add_all([first, second])
        audit(db, 1, first, 'imported')
        audit(db, 1, second, 'imported')
        first.kind = 'limitation'
        first.reference = 'https://example.test/current'
        first.content = 'New note content'
        db.commit()
        paths, members = _render(db, 1)
        assert paths == {1: 'sources/documents/1.md', 2: 'sources/documents/2.txt'}
        assert members == {paths[1]: original, paths[2]: b''}
    engine.dispose()


def test_archived_upload_skips_foreign_missing_and_stale_ownership(tmp_path):
    engine, _ = initialize(tmp_path)
    filename = 'a' * 32 + '.txt'
    with Session(engine) as db:
        db.add_all([Project(id=1, name='Selected'), Project(id=2, name='Other')])
        db.flush()
        foreign = Note(id=1, project_id=2, kind='document', title='Foreign source',
                       reference=filename, content='Foreign bytes')
        old = Note(id=2, project_id=1, kind='document', title='Old source', created_at='old',
                   reference='b' * 32 + '.md', content='Deleted source bytes')
        fake = Note(id=3, project_id=1, kind='document', title='Editable reference',
                    reference=filename, content='Do not copy files using this reference')
        missing = Note(id=4, project_id=1, kind='document', title='Missing provenance')
        unsafe = Note(id=5, project_id=1, kind='document', title='Unsafe original',
                      reference='../separate-file.txt', content='Untrusted filename')
        nul = Note(id=6, project_id=1, kind='document', title='Malformed original',
                   reference='c' * 32 + '.txt', content='Malformed\x00content')
        db.add_all([foreign, old, fake, missing, unsafe, nul])
        for project_id, note in ((2, foreign), (1, old), (1, unsafe), (1, nul)):
            audit(db, project_id, note, 'imported')
        db.add(AuditEvent(project_id=1, entity_type='notes', entity_id=missing.id, action='imported',
                          after={'kind': 'document', 'reference': filename, 'created_at': missing.created_at}))
        db.commit()
        old_reference = old.reference
        db.delete(old)
        db.flush()
        db.add(Note(id=2, project_id=1, kind='document', title='Reused note ID', created_at='new',
                    reference=old_reference, content='Current note'))
        db.commit()

        assert _render(db, 1) == ({}, {})
        paths, members = _render(db, 2)
        assert paths == {1: 'sources/documents/1.txt'}
        assert members == {paths[1]: b'Foreign bytes'}
    engine.dispose()
