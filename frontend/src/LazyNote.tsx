import { useEffect, useState, type ReactNode } from 'react';
import { Feedback } from './Feedback';
import { loadNote, noteRequestIdentity } from './noteLoading';
import type { RecordData } from './types';

type LoadedState = { identity: string; attempt: number; note: RecordData | null; error: string };

/** Content belongs to this mounted reader, never to a cross-project/global cache. */
export function useLoadedNote(base: string, note: RecordData, enabled = true) {
  const identity = noteRequestIdentity(base, note);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<LoadedState | null>(null);
  useEffect(() => {
    if (!enabled) {
      setState(null);
      return;
    }
    const abort = new AbortController();
    setState(null);
    loadNote(base, note, abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setState({ identity, attempt, note: value, error: '' });
      })
      .catch((error: unknown) => {
        if (!abort.signal.aborted) setState({ identity, attempt, note: null,
          error: error instanceof Error ? error.message : 'Could not load this note.' });
      });
    return () => abort.abort();
  }, [identity, attempt, enabled, note.content, note.content_loaded]);
  // Identity-check during render too: effects run after the first new-project frame.
  const current = enabled && state?.identity === identity && state.attempt === attempt ? state : null;
  return { note: current?.note ?? null, loading: enabled && !current,
    error: current?.error ?? '', retry: () => setAttempt((value) => value + 1) };
}

export function LazyNote({ base, note, children }: {
  base: string; note: RecordData; children: (loaded: RecordData) => ReactNode;
}) {
  const loaded = useLoadedNote(base, note);
  if (loaded.loading) return <Feedback>Loading saved content…</Feedback>;
  if (loaded.error) return <>
    <Feedback tone="error">{loaded.error}</Feedback>
    <button type="button" className="secondary" onClick={loaded.retry}>Retry loading content</button>
  </>;
  return loaded.note ? children(loaded.note) : null;
}
