import { useEffect, useState } from 'react';
import { api } from './api';
import { Empty, ErrorBox } from './ui';

type Kind = 'all' | 'tasks' | 'notes' | 'conversations' | 'qa' | 'knowledge' | 'sessions' | 'other';
type Group = {
  kind: Exclude<Kind, 'all'>;
  title: string;
  results: { id: string; title: string; snippet: string; href?: string }[];
  next_offset: number | null;
};
const filters: [Kind, string][] = [
  ['all', 'All'], ['tasks', 'TODOs'], ['notes', 'Notes'], ['conversations', 'Conversations'],
  ['qa', 'AI Q&A'], ['knowledge', 'Knowledge'], ['sessions', 'Sessions'], ['other', 'Other records'],
];

export function ProjectSearchResults({ base, query, onOpenRecord, onOpenSource }: {
  base: string; query: string;
  onOpenRecord: (kind: string, id: string, title: string) => void;
  onOpenSource: () => void;
}) {
  const [kind, setKind] = useState<Kind>('all');
  const [offsets, setOffsets] = useState([0]);
  const [data, setData] = useState<{ groups: Group[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    setData(null);
    setError('');
    const params = new URLSearchParams({ q: query, kind, offset: String(offsets.at(-1)) });
    api<{ groups: Group[] }>(`${base}/search/all?${params}`, { signal: abort.signal })
      .then((result) => { if (!abort.signal.aborted) setData(result); })
      .catch((e) => { if (!abort.signal.aborted) setError(e.message); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [base, query, kind, offsets]);
  function select(next: Kind) {
    setKind(next);
    setOffsets([0]);
  }
  const groups = data?.groups ?? [];
  const next = groups[0]?.next_offset;
  return <section className="card" aria-busy={loading}>
    <h2>Search results for “{query}”</h2>
    <div className="search-result-filters" role="group" aria-label="Search result types">
      {filters.map(([value, name]) => <button key={value} type="button"
        className="secondary" aria-pressed={kind === value} onClick={() => select(value)}>{name}</button>)}
    </div>
    <ErrorBox error={error} />
    {loading && <Empty>Searching saved project information…</Empty>}
    {!loading && !error && !groups.some((group) => group.results.length) && <Empty>No matching records in this project.</Empty>}
    {groups.filter((group) => group.results.length).map((group) => <section className="brief-section" key={group.kind}>
      <h3>{group.title}</h3>
      {group.results.map((result) => <article className="list-item" key={result.id}>
        <h4>{result.href && (result.href.startsWith('#history?') || result.href.startsWith('#knowledge?'))
          ? <a href={result.href} onClick={onOpenSource}>{result.title}</a> : result.title}</h4>
        <p>{result.snippet}</p>
        {['tasks', 'notes', 'qa', 'sessions'].includes(group.kind) && <button type="button" className="quiet"
          onClick={() => onOpenRecord(group.kind, result.id, result.title)}>Open</button>}
      </article>)}
      {kind === 'all' && group.next_offset !== null && <button type="button" className="quiet"
        onClick={() => select(group.kind)}>More {group.title}</button>}
    </section>)}
    {kind !== 'all' && (offsets.length > 1 || next != null) && <nav className="pagination" aria-label="Project search pages">
      <button type="button" className="secondary" disabled={loading || offsets.length === 1}
        onClick={() => setOffsets((previous) => previous.slice(0, -1))}>Previous</button>
      <span role="status">Page {offsets.length}</span>
      <button type="button" className="secondary" disabled={loading || next == null}
        onClick={() => { if (next != null) setOffsets((previous) => [...previous, next]); }}>Next</button>
    </nav>}
  </section>;
}
