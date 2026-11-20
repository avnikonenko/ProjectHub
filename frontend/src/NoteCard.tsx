import { useEffect, useRef, type ReactNode } from 'react';
import { useLoadedNote } from './LazyNote';
import { NoteContent } from './NoteContent';
import { Badge, DeleteButton, EditButton, ErrorBox } from './ui';
import { Feedback } from './Feedback';
import type { RecordData } from './types';

export function NoteCard({ base, note, busy, reveal, reference, editRequest, onEditIntent, onEditReady, onEditCancel, onDelete }: {
  base: string; note: RecordData; busy: boolean; reveal: boolean;
  reference?: ReactNode; editRequest?: number;
  onEditIntent: () => void; onEditReady: (note: RecordData, identity: number) => void;
  onEditCancel: (identity: number) => void; onDelete: () => void;
}) {
  const cancelEdit = useRef(onEditCancel);
  cancelEdit.current = onEditCancel;
  const pendingEdit = editRequest !== undefined;
  const loaded = useLoadedNote(base, note);
  useEffect(() => () => {
    // Pagination/unmount must not retain an edit intent that could reopen when
    // the card returns. Callback identity alone changes on ordinary renders.
    if (editRequest !== undefined) cancelEdit.current(editRequest);
  }, [editRequest]);
  useEffect(() => {
    if (editRequest !== undefined && loaded.note) {
      onEditReady(loaded.note, editRequest);
    }
  }, [editRequest, loaded.note, onEditReady]);
  const status = <>
    {loaded.loading && <Feedback>Loading saved content…</Feedback>}
    <ErrorBox error={loaded.error} />
    {loaded.error && <button type="button" className="secondary" onClick={loaded.retry}>Retry loading content</button>}
  </>;
  return <section className="card note-card">
    <div className="section-title">
      <h2>{note.title}</h2>
      <div className="note-actions">
        <Badge value={note.kind} />
        <EditButton label={`Edit note: ${note.title}`} disabled={busy || pendingEdit}
          onClick={onEditIntent} />
        <DeleteButton label={`Delete note: ${note.title}`} disabled={busy} onClick={onDelete} />
      </div>
    </div>
    {editRequest !== undefined && <button type="button" className="quiet" onClick={() => onEditCancel(editRequest)}>Cancel opening editor</button>}
    {status}{loaded.note && <NoteContent content={loaded.note.content} reveal={reveal} />}
    {reference}
  </section>;
}
