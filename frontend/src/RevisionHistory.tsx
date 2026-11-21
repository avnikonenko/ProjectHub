import { Feedback } from './Feedback';
import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { DeleteButton } from './ui';
import { SavedGeneration, type GenerationProvenance } from './GenerationStatus';

type Revision = { id: number; generated_at: string; generation?: GenerationProvenance | null };
type RevisionPage = { results: Revision[]; next_offset: number | null; active_id?: number | null };

export function RevisionHistory<T>({
  base, kind, version, disabled, selectedId, onSelect, onLoadingChange, onActivated, onActiveRead,
}: {
  base: string;
  kind: 'brief' | 'qa' | 'scheme' | 'parameters' | 'knowledge';
  version: string | null;
  disabled: boolean;
  selectedId: number | null;
  onSelect: (snapshot: T | null, revisionId: number | null) => void;
  onLoadingChange: (loading: boolean, activating?: boolean) => void;
  onActivated?: () => void;
  onActiveRead?: (result: unknown) => void;
}) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState<RevisionPage | null>(null);
  const [offsets, setOffsets] = useState([0]);
  const [loading, setLoading] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const busy = useRef(false);
  const request = useRef(0);
  const current = useRef({ onSelect, onLoadingChange, onActivated, onActiveRead, disabled });
  current.current = { onSelect, onLoadingChange, onActivated, onActiveRead, disabled };
  const prefix = `${base}/codex`;

  useEffect(() => {
    setPage(null);
    setOffsets([0]);
    setError('');
  }, [version, prefix, kind]);

  useEffect(() => {
    if (!open || disabled) return;
    const abort = new AbortController();
    setLoading(true);
    const query = new URLSearchParams({ kind, offset: String(offsets[offsets.length - 1]) });
    api<RevisionPage>(`${prefix}/revisions?${query}`, { signal: abort.signal })
      .then((result) => {
        if (!abort.signal.aborted) { setPage(result); setError(''); }
      })
      .catch((e) => { if (!abort.signal.aborted) setError(e.message); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [open, disabled, version, prefix, kind, offsets]);

  useEffect(() => () => { request.current += 1; }, []);

  function isActive(revision: Revision) {
    return page?.active_id === undefined ? revision.generated_at === version : revision.id === page.active_id;
  }

  async function select(id: number | null, activate = false) {
    if (current.current.disabled || busy.current) return;
    busy.current = true;
    setSelecting(true);
    const requestId = ++request.current;
    current.current.onLoadingChange(true, activate);
    setError('');
    setNotice('');
    try {
      if (id === null && current.current.onActiveRead) {
        const result = await api<unknown>(`${prefix}/${kind}`);
        if (request.current === requestId) {
          current.current.onActiveRead?.(result);
          setOffsets([0]);
        }
        return;
      }
      let snapshot: T | null;
      if (id === null) {
        snapshot = kind === 'scheme' || kind === 'parameters' || kind === 'knowledge'
          ? (await api<{ snapshot: T | null }>(`${prefix}/${kind}`)).snapshot
          : await api<T | null>(`${prefix}/${kind}`);
      } else {
        const url = `${prefix}/revisions/${id}`;
        const result = await api<{ kind: string; snapshot: T }>(
          activate ? `${url}/activate` : url, activate ? { method: 'POST' } : undefined,
        );
        if (result.kind !== kind) throw new Error('This saved version belongs to a different view.');
        snapshot = result.snapshot;
      }
      if (request.current === requestId) {
        current.current.onSelect(snapshot, activate ? null : id);
        if (activate) {
          setOffsets([0]);
          setNotice('This version is now active.');
          current.current.onActivated?.();
        }
      }
    } catch (e) {
      if (request.current === requestId) setError((e as Error).message);
    } finally {
      busy.current = false;
      if (request.current === requestId) {
        setSelecting(false);
        current.current.onLoadingChange(false);
      }
    }
  }

  async function remove(revision: Revision) {
    if (disabled || busy.current || isActive(revision)) return;
    if (!window.confirm(`Delete the saved overview from ${new Date(revision.generated_at).toLocaleString()}?`)) return;
    busy.current = true;
    setSelecting(true);
    onLoadingChange(true);
    setError('');
    setNotice('');
    try {
      const active = selectedId === revision.id ? await api<T | null>(`${prefix}/brief`) : undefined;
      await api(`${prefix}/revisions/${revision.id}`, { method: 'DELETE' });
      if (active !== undefined) current.current.onSelect(active, null);
      setPage(null);
      setOffsets([0]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      busy.current = false;
      setSelecting(false);
      current.current.onLoadingChange(false);
    }
  }

  return (
    <div className="revision-history">
      {selectedId !== null && (
        <div className="toolbar">
          <strong role="status">Viewing saved version</strong>
          <button className="secondary" disabled={disabled || selecting} onClick={() => select(selectedId, true)}>
            Use this version
          </button>
          <button className="quiet" disabled={disabled || selecting} onClick={() => select(null)}>
            Return to active version
          </button>
        </div>
      )}
      <Feedback tone="success">{notice}</Feedback>
      <Feedback tone="error">{error}</Feedback>
      <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary>Previous versions</summary>
        {loading && !disabled && <Feedback>Loading saved versions…</Feedback>}
        {!loading && page && !page.results.length && <p>No previous versions saved.</p>}
        {!!page?.results.length && (
          <ul>
            {page.results.map((revision) => {
              const active = isActive(revision);
              return (
                <li key={revision.id}>
                  <button className="quiet" disabled={disabled || selecting || selectedId === revision.id
                    || (active && selectedId === null)} onClick={() => select(active ? null : revision.id)}>
                    {new Date(revision.generated_at).toLocaleString()}
                    {active ? ' · Active' : ''}
                    {selectedId === revision.id ? ' · Viewing' : ''}
                  </button>
                  {kind === 'brief' && !active && (
                    <DeleteButton label={`Delete overview from ${new Date(revision.generated_at).toLocaleString()}`}
                      disabled={disabled || selecting || loading} onClick={() => void remove(revision)} />
                  )}
                  <SavedGeneration generation={revision.generation} />
                </li>
              );
            })}
          </ul>
        )}
        {(offsets.length > 1 || page?.next_offset != null) && (
          <nav className="pagination" aria-label="Saved version pages">
            <button className="secondary" disabled={disabled || loading || offsets.length === 1}
              onClick={() => setOffsets(offsets.slice(0, -1))}>Newer versions</button>
            <span>Page {offsets.length}</span>
            <button className="secondary" disabled={disabled || loading || page?.next_offset == null}
              onClick={() => { if (page?.next_offset != null) setOffsets([...offsets, page.next_offset]); }}>
              Older versions
            </button>
          </nav>
        )}
      </details>
    </div>
  );
}
