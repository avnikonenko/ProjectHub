import { useEffect, useState } from 'react';
import { api, post } from './api';
import { historyTurnHref } from './historyLinks';

type IndexStatus = { indexed_at: string | null; indexed_turns: number };
type Match = { session_id: number; session_title: string; turn_id: string; snippet: string };
type SearchPage = IndexStatus & { results: Match[]; next_offset: number | null };
type SearchResult = { query: string; offsets: number[]; page: SearchPage };

export function HistorySearch({
  prefix, projectId, onIndexed,
}: {
  prefix: string;
  projectId: number;
  onIndexed: () => void;
}) {
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState<SearchResult | null>(null);
  const [index, setIndex] = useState<IndexStatus | null>(null);
  const [busy, setBusy] = useState<'status' | 'index' | 'search' | null>('status');
  const [error, setError] = useState('');
  useEffect(() => {
    const abort = new AbortController();
    api<SearchPage>(`${prefix}/history/search?q=&offset=0`, { signal: abort.signal })
      .then((page) => { if (!abort.signal.aborted) setIndex(page); })
      .catch((e) => { if (!abort.signal.aborted) setError(e.message); })
      .finally(() => { if (!abort.signal.aborted) setBusy(null); });
    return () => abort.abort();
  }, [prefix]);

  async function loadSearch(value: string, offsets: number[]) {
    const params = new URLSearchParams({ q: value, offset: String(offsets[offsets.length - 1]) });
    const page = await api<SearchPage>(`${prefix}/history/search?${params}`);
    setIndex(page);
    setSearch({ query: value, offsets, page });
  }

  async function runSearch(value: string, offsets = [0]) {
    if (busy || !value) return;
    setBusy('search');
    setError('');
    try {
      await loadSearch(value, offsets);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function refreshIndex() {
    if (busy) return;
    setBusy('index');
    setError('');
    try {
      setIndex(await post(`${prefix}/history/index`));
      onIndexed();
      if (search) {
        setSearch(null);
        await loadSearch(search.query, [0]);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card history-search" aria-busy={!!busy}>
      <div className="section-title">
        <h2>Search conversation history</h2>
        <button className="secondary" disabled={!!busy} onClick={refreshIndex}>
          {busy === 'index' ? 'Indexing…' : index?.indexed_at ? 'Refresh history index' : 'Index history'}
        </button>
      </div>
      <p>Search across linked sessions. Indexing and search run locally and do not send text to OpenAI.</p>
      <p role="status">
        {busy === 'status' ? 'Checking history index…' :
          busy === 'index' ? 'Reading linked sessions. Large histories may take a while.' :
          index?.indexed_at ?
            `${index.indexed_turns.toLocaleString()} indexed turns · Updated ${new Date(index.indexed_at).toLocaleString()}` :
            'Index the history to search saved turns.'}
      </p>
      {error && <div className="error" role="alert">{error}</div>}
      <form className="search-bar" role="search" aria-label="Conversation history" onSubmit={(event) => {
        event.preventDefault();
        void runSearch(query.trim());
      }}>
        <input
          type="search"
          aria-label="Search linked conversations"
          placeholder="Search linked conversations…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button disabled={!!busy || !index?.indexed_at || !query.trim()}>Search</button>
      </form>
      {search && (
        <>
          <h3>Results for “{search.query}”</h3>
          {!search.page.results.length && <p>No matching indexed turns.</p>}
          {search.page.results.map((result) => (
            <article className="list-item" key={`${result.session_id}/${result.turn_id}`}>
              <h4>
                <a href={historyTurnHref({
                  projectId, sessionId: result.session_id, turnId: result.turn_id,
                })}>
                  {result.session_title || `Session #${result.session_id}`} · Turn {result.turn_id}
                </a>
              </h4>
              <p>{result.snippet}</p>
            </article>
          ))}
          {(search.offsets.length > 1 || search.page.next_offset !== null) && (
            <nav className="pagination" aria-label="Conversation search pages">
              <button className="secondary" disabled={!!busy || search.offsets.length === 1}
                onClick={() => runSearch(search.query, search.offsets.slice(0, -1))}>Previous</button>
              <span role="status">{busy === 'search' ? 'Searching…' : `Page ${search.offsets.length}`}</span>
              <button className="secondary" disabled={!!busy || search.page.next_offset === null}
                onClick={() => {
                  if (search.page.next_offset !== null) {
                    void runSearch(search.query, [...search.offsets, search.page.next_offset]);
                  }
                }}>Next</button>
            </nav>
          )}
        </>
      )}
    </section>
  );
}
