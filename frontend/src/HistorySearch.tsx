import { Feedback } from './Feedback';
import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { startReviewPolling } from './reviewPolling';

type IndexStatus = { indexed_at: string | null; indexed_turns: number };

export function HistorySearch({
  base, onIndexed,
}: {
  base: string;
  onIndexed: () => void;
}) {
  const [index, setIndex] = useState<IndexStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [indexing, setIndexing] = useState(false);
  const indexAbort = useRef<AbortController | null>(null);
  const busy = indexing ? 'index' : statusLoading ? 'status' : null;
  const [error, setError] = useState('');
  const [syncError, setSyncError] = useState('');
  const [autoSync, setAutoSync] = useState(true);
  useEffect(() => startReviewPolling(
    signal => api<IndexStatus & { enabled: boolean; error: string | null }>(
      `${base}/codex/history/sync`, { signal }),
    status => {
      setAutoSync(status.enabled);
      setSyncError(status.error ?? '');
      setIndex(status);
    },
    () => setSyncError('Could not check automatic history sync. Manual refresh is still available.'),
    15000,
  ), [base]);
  useEffect(() => {
    const abort = new AbortController();
    setStatusLoading(true);
    setIndex(null);
    setError('');
    api<IndexStatus>(`${base}/history/search?q=&offset=0`, { signal: abort.signal })
      .then((page) => { if (!abort.signal.aborted) setIndex(page); })
      .catch((e) => { if (!abort.signal.aborted) setError(e.message); })
      .finally(() => { if (!abort.signal.aborted) setStatusLoading(false); });
    return () => abort.abort();
  }, [base]);

  useEffect(() => {
    setIndexing(false);
    indexAbort.current = null;
    return () => { indexAbort.current?.abort(); };
  }, [base]);

  async function refreshIndex() {
    if (busy || indexAbort.current) return;
    const abort = new AbortController();
    indexAbort.current = abort;
    setIndexing(true);
    setError('');
    try {
      const status = await api<IndexStatus>(`${base}/codex/history/index`, {
        method: 'POST', body: '{}', signal: abort.signal,
      });
      if (!abort.signal.aborted) {
        setIndex(status);
        onIndexed();
      }
    } catch (e) {
      if (!abort.signal.aborted) setError((e as Error).message);
    } finally {
      if (indexAbort.current === abort) indexAbort.current = null;
      if (!abort.signal.aborted) setIndexing(false);
    }
  }

  return (
    <section className="card history-search" aria-busy={!!busy}>
      <div className="section-title">
        <h2>Conversation index</h2>
        <button className="secondary" disabled={!!busy} onClick={refreshIndex}>
          {busy === 'index' ? 'Indexing…' : index?.indexed_at ? 'Refresh Codex index' : 'Index Codex history'}
        </button>
      </div>
      <p>Keep saved Codex conversations available to project search. Imported ChatGPT conversations are already searchable.</p>
      <p role="status">
        {busy === 'status' ? 'Checking history index…' :
          busy === 'index' ? 'Reading linked sessions. Large histories may take a while.' :
          index?.indexed_at ?
            `${index.indexed_turns.toLocaleString()} indexed turns · Updated ${new Date(index.indexed_at).toLocaleString()}` :
            autoSync ? 'Saved Codex history will sync automatically. Imported chats are already searchable.'
              : 'Imported chats are already searchable. Index Codex history to include saved turns.'}
      </p>
      <Feedback tone="error">{syncError}</Feedback>
      <Feedback tone="error">{error}</Feedback>
    </section>
  );
}
