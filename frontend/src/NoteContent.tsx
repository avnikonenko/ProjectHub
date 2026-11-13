import { useEffect, useId, useRef, useState } from 'react';
import { Markdown } from './ui';

export function NoteContent({ content, reveal = false }: { content?: string; reveal?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const [long, setLong] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  const id = useId();
  const open = expanded || reveal;
  useEffect(() => {
    const element = body.current;
    if (!element) return;
    function measure() {
      if (element) setLong(element.scrollHeight > parseFloat(getComputedStyle(document.documentElement).fontSize) * 13 + 1);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    measure();
    return () => observer.disconnect();
  }, [content]);
  return (
    <div className="note-reading">
      <div id={id} ref={body} className={open ? 'note-body' : 'note-body note-preview'}>
        <Markdown preview={long && !open}>{content}</Markdown>
      </div>
      {(long || expanded) && !reveal && (
        <button type="button" className="quiet" aria-expanded={open} aria-controls={id}
          onClick={() => setExpanded((value) => !value)}>
          {open ? 'Show less' : 'Expand note'}
        </button>
      )}
    </div>
  );
}
