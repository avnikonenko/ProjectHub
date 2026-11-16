import { useEffect, useState, type ReactNode } from 'react';
import { useLoadedNote } from './LazyNote';
import { NoteContent } from './NoteContent';
import { Badge, DeleteButton, EditButton, ErrorBox } from './ui';
import { Feedback } from './Feedback';
import type { RecordData } from './types';

export function NoteCard({ base, note, shared, busy, reveal, reference, onEdit, onDelete }: {
  base: string; note: RecordData; shared: boolean; busy: boolean; reveal: boolean;
  reference?: ReactNode; onEdit: (note: RecordData) => void; onDelete: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [pendingEdit, setPendingEdit] = useState(false);
  const loaded = useLoadedNote(base, note, !shared || expanded || pendingEdit);
  useEffect(() => {
    if (pendingEdit && loaded.note) {
      setPendingEdit(false);
      onEdit(loaded.note);
    }
  }, [pendingEdit, loaded.note, onEdit]);
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
          onClick={() => {
            if (loaded.note) onEdit(loaded.note);
            else setPendingEdit(true);
          }} />
        <DeleteButton label={`Delete note: ${note.title}`} disabled={busy} onClick={onDelete} />
      </div>
    </div>
    {pendingEdit && <button type="button" className="quiet" onClick={() => setPendingEdit(false)}>Cancel opening editor</button>}
    {shared ? <>
      {pendingEdit && !expanded && status}
      <details open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}>
        <summary>Read transcript</summary>
        {expanded && <>{status}{loaded.note && <div className="shared-transcript">{loaded.note.content}</div>}</>}
      </details>
    </> : <>{status}{loaded.note && <NoteContent content={loaded.note.content} reveal={reveal} />}</>}
    {reference}
  </section>;
}
