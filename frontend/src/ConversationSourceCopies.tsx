import { useEffect, useState } from 'react';
import { api } from './api';
import { conversationSourcesPath, parseConversationSources, type ConversationSource } from './conversationImportData';
import { DeferredDetails } from './DeferredDetails';
import { Feedback } from './Feedback';
import { ErrorBox } from './ui';
import type { RecordData } from './types';

export function ConversationSourceCopies({ base, note }: { base: string; note: RecordData }) {
  return <DeferredDetails summary={<summary>Source copies</summary>}>
    {() => <SourceCopies key={JSON.stringify([base, note.id, note.updated_at])} base={base} note={note} />}
  </DeferredDetails>;
}

function SourceCopies({ base, note }: { base: string; note: RecordData }) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ path: string; sources: ConversationSource[]; error: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setState(null);
    async function load() {
      try {
        const path = conversationSourcesPath(base, note);
        const raw = await api<unknown>(path, { signal: controller.signal });
        if (!controller.signal.aborted) setState({ path, sources: parseConversationSources(raw), error: '' });
      } catch (error) {
        if (!controller.signal.aborted) setState({ path: '', sources: [], error: (error as Error).message });
      }
    }
    void load();
    return () => controller.abort();
  }, [base, note.id, note.project_id, attempt]);
  if (!state) return <Feedback>Loading source copies…</Feedback>;
  if (state.error) return <><ErrorBox error={state.error} />
    <button type="button" className="secondary" onClick={() => setAttempt(value => value + 1)}>Retry loading source copies</button>
  </>;
  return state.sources.length ? <ul>{state.sources.map(source => {
    const date = Date.parse(source.created_at);
    return <li key={source.id}>
      <a href={`/api${state.path}/${source.id}`} download>{source.filename}</a>
      {Number.isFinite(date) && <small> · Saved {new Date(date).toLocaleString()}</small>}
    </li>;
  })}</ul> : <p className="muted">No original source copies are available.</p>;
}
