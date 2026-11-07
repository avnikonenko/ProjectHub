import { useEffect, useRef, useState } from 'react';
import { post } from './api';
import { Form, titleField } from './ui';

export type PassageNoteContext = { base: string; projectId: number; onSaved: () => Promise<void> };

export function SaveablePassage({ context, reference, title, children }: {
  context: PassageNoteContext;
  reference: string;
  title: string;
  children: React.ReactNode;
}) {
  const content = useRef<HTMLDivElement>(null);
  const [selection, setSelection] = useState('');
  const [excerpt, setExcerpt] = useState('');
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    function update() {
      const selected = window.getSelection();
      const inside = content.current?.contains(selected?.anchorNode ?? null)
        && content.current?.contains(selected?.focusNode ?? null);
      setSelection(inside ? selected?.toString().trim() ?? '' : '');
    }
    document.addEventListener('selectionchange', update);
    return () => document.removeEventListener('selectionchange', update);
  }, []);
  return (
    <>
      <div ref={content}>{children}</div>
      {selection && (
        <button className="secondary" type="button" onMouseDown={(event) => event.preventDefault()}
          onClick={() => { setExcerpt(selection); setSaved(false); }}>Save passage as note</button>
      )}
      {excerpt && (
        <Form title="Save passage as note" fields={[
          { ...titleField, maxLength: 500 },
          { name: 'comment', label: 'Comment (optional)', type: 'textarea' },
        ]} initial={{ title: title.slice(0, 500) }} onClose={() => setExcerpt('')}
          onSave={async ({ title: noteTitle, comment }) => {
            const quote = excerpt.split(/\r?\n/).map((line) => `> ${line}`).join('\n');
            await post(`${context.base}/notes`, {
              title: String(noteTitle).trim(), reference,
              content: comment.trim() ? `${quote}\n\n${comment.trim()}` : quote,
            });
            setSaved(true);
            await context.onSaved();
          }} />
      )}
      {saved && <small role="status">Saved to Notes.</small>}
    </>
  );
}
