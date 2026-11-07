import { useEffect, useState } from 'react';
import { api, post } from './api';
import { chatGPTTranscriptHref, historyTurnHref } from './historyLinks';

type IndexStatus = { indexed_at: string | null; indexed_turns: number };
type Match =
  | { kind: 'codex'; session_id: number; session_title: string; turn_id: string; snippet: string }
  | { kind: 'chatgpt'; note_id: number; title: string; snippet: string };
type SearchPage = IndexStatus & { results: Match[]; next_offset: number | null };
type SearchResult = { query: string; offsets: number[]; page: SearchPage };

export function HistorySearch({
  base, projectId, onIndexed,
}: {
  base: string;
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
    api<SearchPage>(`${base}/history/search?q=&offset=0`, { signal: abort.signal })
      .then((page) => { if (!abort.signal.aborted) setIndex(page); })
      .catch((e) => { if (!abort.signal.aborted) setError(e.message); })
      .finally(() => { if (!abort.signal.aborted) setBusy(null); });
    return () => abort.abort();
  }, [base]);

  async function loadSearch(value: string, offsets: number[]) {
    const params = new URLSearchParams({ q: value, offset: String(offsets[offsets.length - 1]) });
    const page = await api<SearchPage>(`${base}/history/search?${params}`);
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
      setIndex(await post(`${base}/codex/history/index`));
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
          {busy === 'index' ? 'Indexing…' : index?.indexed_at ? 'Refresh Codex index' : 'Index Codex history'}
        </button>
      </div>
      <p>Search indexed Codex turns and imported ChatGPT conversations. Search runs locally.</p>
      <p role="status">
        {busy === 'status' ? 'Checking history index…' :
          busy === 'index' ? 'Reading linked sessions. Large histories may take a while.' :
          index?.indexed_at ?
            `${index.indexed_turns.toLocaleString()} indexed turns · Updated ${new Date(index.indexed_at).toLocaleString()}` :
            'Imported chats are already searchable. Index Codex history to include saved turns.'}
      </p>
      {error && <div className="error" role="alert">{error}</div>}
      <form className="search-bar" role="search" aria-label="Conversation history" onSubmit={(event) => {
        event.preventDefault();
        void runSearch(query.trim());
      }}>
        <input
          type="search"
          aria-label="Search conversations"
          placeholder="Search conversations…"
          maxLength={2000}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button disabled={!!busy || !query.trim()}>Search</button>
      </form>
      {search && (
        <>
          <h3>Results for “{search.query}”</h3>
          {!search.page.results.length && <p>No matching conversations.</p>}
          {search.page.results.map((result) => {
            const href = result.kind === 'chatgpt'
              ? chatGPTTranscriptHref({ projectId, noteId: result.note_id, query: search.query })
              : historyTurnHref({ projectId, sessionId: result.session_id, turnId: result.turn_id });
            return (
              <article className="list-item" key={href}>
                <h4>
                  <a href={href}>
                    {result.kind === 'chatgpt'
                      ? `${result.title} · ChatGPT`
                      : `${result.session_title || `Session #${result.session_id}`} · Codex turn ${result.turn_id}`}
                  </a>
                </h4>
                <p>{result.snippet}</p>
              </article>
            );
          })}
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
