import { AIEmphasis } from './AIEmphasis';
import { DeferredDetails } from './DeferredDetails';
import { SearchSnippet, type SearchSnippetData } from './SearchSnippet';
import { useEffect, useId, useRef, useState } from 'react';
import { api } from './api';
import { chatGPTTranscriptHref, historyTurnHref } from './historyLinks';
import { evidenceAvailability, filterKnowledge, knowledgePassageGroups, visibleKnowledgeGroups, visibleRelatedKnowledge, knowledgeSourceHref, statusOrder,
  type KnowledgeItem, type KnowledgeSnapshot } from './knowledge';
import { ALL_TOPICS, filterTopicItems, topicChoices } from './topicGroups';
import { TopicPicker } from './TopicPicker';
import './knowledge.css';

const display = (value: string) => value.replaceAll('_', ' ');

type ConversationHit = SearchSnippetData & (
  { kind: 'codex'; session_id: number; session_title: string; turn_id: string }
  | { kind: 'chatgpt'; note_id: number; title: string }
);
type ConversationPage = { results: ConversationHit[]; next_offset: number | null };

export function searchKnowledgeHistory(projectId: number, query: string, offset: number, signal: AbortSignal,
  request: typeof api = api) {
  const params = new URLSearchParams({ q: query.trim().slice(0, 2000), offset: String(offset) });
  return request<ConversationPage>(`/projects/${projectId}/history/search?${params}`, { signal });
}

export function KnowledgeHistorySearch({ projectId, query }: { projectId: number; query: string }) {
  const [offsets, setOffsets] = useState<number[]>([]);
  const [page, setPage] = useState<ConversationPage | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const offset = offsets.at(-1);
    if (offset === undefined) return;
    const abort = new AbortController();
    searchKnowledgeHistory(projectId, query, offset, abort.signal)
      .then(result => { if (!abort.signal.aborted) setPage(result); })
      .catch((failure: Error) => { if (!abort.signal.aborted) setError(failure.message); });
    return () => abort.abort();
  }, [projectId, query, offsets]);

  function load(next: number[]) {
    setPage(null);
    setError('');
    setOffsets(next);
  }

  if (!offsets.length) return <button type="button" className="quiet" onClick={() => load([0])}>
    Search original conversations
  </button>;
  return <section className="knowledge-group" aria-label="Original conversation results" aria-busy={!page && !error}>
    <h3>Original conversations</h3>
    {!page && !error && <p role="status">Searching indexed conversations…</p>}
    {error && <><p role="alert">Could not search original conversations. {error}</p>
      <button type="button" className="secondary" onClick={() => load([...offsets])}>Retry search</button></>}
    {page && !page.results.length && <p>No original conversations match this search.</p>}
    {page?.results.map(hit => {
      const href = hit.kind === 'chatgpt' ? chatGPTTranscriptHref({ projectId, noteId: hit.note_id, query })
        : historyTurnHref({ projectId, sessionId: hit.session_id, turnId: hit.turn_id });
      return <article key={href} className="list-item">
        <a href={href}>{hit.kind === 'chatgpt' ? hit.title : hit.session_title}</a>
        <SearchSnippet snippet={hit.snippet} snippet_parts={hit.snippet_parts} />
      </article>;
    })}
    {page && (offsets.length > 1 || page.next_offset != null) && <nav className="pagination" aria-label="Original conversation pages">
      <button type="button" className="secondary" disabled={offsets.length === 1}
        onClick={() => load(offsets.slice(0, -1))}>Previous</button>
      <span role="status">Page {offsets.length}</span>
      <button type="button" className="secondary" disabled={page.next_offset == null}
        onClick={() => { if (page.next_offset != null) load([...offsets, page.next_offset]); }}>Next</button>
    </nav>}
  </section>;
}

export function KnowledgeItems({ snapshot, availability = {}, selectedItemId, onSelectItem, historical = false }: {
  snapshot: KnowledgeSnapshot; availability?: Record<string, string>;
  selectedItemId?: string; onSelectItem?: (id: string) => void;
  historical?: boolean;
}) {
  const [query, setQuery] = useState('');
  const [topic, setTopic] = useState(ALL_TOPICS);
  const [tagSearch, setTagSearch] = useState('');
  const [status, setStatus] = useState('');
  const [category, setCategory] = useState('');
  const [scope, setScope] = useState('');
  const [selected, setSelected] = useState(selectedItemId);
  const [selectionRequest, setSelectionRequest] = useState(0);
  const [relatedLimits, setRelatedLimits] = useState<Record<string, number>>({});
  const [limits, setLimits] = useState<Record<string, number>>({});
  const instanceId = useId();
  const selectedRef = useRef<HTMLDetailsElement | null>(null);
  const identity = `${snapshot.project_id}:${snapshot.publication_id || snapshot.generated_at || ''}`;
  const reset = () => { setQuery(''); setTagSearch(''); setTopic(ALL_TOPICS); setStatus(''); setCategory(''); setScope(''); setLimits({}); setRelatedLimits({}); };
  useEffect(() => { reset(); setSelected(selectedItemId); }, [identity, selectedItemId]);
  useEffect(() => {
    if (selectedRef.current && selected) {
      selectedRef.current.open = true;
      selectedRef.current.scrollIntoView?.({ block: 'nearest' });
      selectedRef.current.querySelector('summary')?.focus({ preventScroll: true });
    }
  }, [selected, identity, selectionRequest]);
  function select(id: string) {
    reset(); setSelected(id); setSelectionRequest((value) => value + 1); onSelectItem?.(id);
  }
  const topics = topicChoices(snapshot.items, snapshot.topic_groups, item => item.id, item => item.design_status);
  const items = filterTopicItems(filterKnowledge(snapshot.items, query, status, category, '', snapshot.sources, scope),
    topic, topics, item => item.id, tagSearch);
  const byId = new Map(snapshot.items.map((item) => [item.id, item]));
  const sources = new Map(snapshot.sources.map((source) => [source.id, source]));
  const statuses = [...new Set([...statusOrder, ...snapshot.items.map((item) => item.design_status)])];
  function relationships(item: KnowledgeItem, field: 'conflicts_with' | 'supersedes' | 'superseded_by', title: string) {
    const incoming = field === 'conflicts_with'
      ? snapshot.items.filter((other) => other.conflicts_with?.includes(item.id)).map((other) => other.id) : [];
    const ids = [...new Set([...(item[field] || []), ...incoming])];
    if (!ids.length) return null;
    return <div className="knowledge-relationships"><strong>{title}</strong><ul>{ids.map((id) => (
      <li key={id}>{byId.has(id)
        ? <button type="button" className="quiet" onClick={() => select(id)}>{byId.get(id)!.statement}</button>
        : <span>Related record unavailable <code>{id}</code></span>}</li>
    ))}</ul></div>;
  }
  function itemDetails(item: KnowledgeItem) {
    const current = evidenceAvailability(item, availability);
    return <DeferredDetails key={item.id} className="knowledge-item" ref={item.id === selected ? selectedRef : undefined}
      id={`${instanceId}-knowledge-item-${encodeURIComponent(item.id)}`}
      openKey={item.id === selected ? `${identity}:${item.id}:${selectionRequest}` : undefined} summary={
      <summary><span className="knowledge-item-title"><AIEmphasis text={item.statement} /></span>
        <small className="knowledge-item-meta"><span>{display(item.design_status)}</span><span>{item.scope}</span>
          <span>{item.evidence.length} {item.evidence.length === 1 ? 'reference' : 'references'}</span>
          {item.reconciliation_state === 'unresolved' && <span>Needs context review</span>}
        </small>
      </summary>}>{() => <div className="knowledge-item-body">
        <p className="muted">Extracted interpretation</p>
        <dl className="knowledge-fields">
          <dt>Design status</dt><dd>{display(item.design_status)}</dd>
          <dt>Category</dt><dd>{display(item.category)}</dd>
          <dt>Scope</dt><dd>{item.scope}</dd>
          {item.rationale && <><dt>Rationale</dt><dd><AIEmphasis text={item.rationale} /></dd></>}
          {item.alternatives && <><dt>Alternatives</dt><dd>{item.alternatives}</dd></>}
          {item.limitations && <><dt>Limitations & trade-offs</dt><dd>{item.limitations}</dd></>}
          <dt>{historical ? 'Evidence at time of generation' : 'Current evidence'}</dt>
          <dd>{display(current)}{!historical && current !== 'valid' && ' — historical design status is preserved.'}</dd>
          {item.reconciliation_state && <><dt>Reconciliation</dt><dd>{display(item.reconciliation_state)}</dd></>}
          {item.reconciliation_note && <><dt>Context review</dt><dd>{item.reconciliation_note}</dd></>}
        </dl>
        {relationships(item, 'conflicts_with', 'Recorded conflict')}
        {relationships(item, 'superseded_by', 'Historical decision — superseded by')}
        {relationships(item, 'supersedes', 'Supersedes')}
        {!!item.relationships?.length && <details><summary>Recorded relationships</summary><ul>{item.relationships.map((relation, index) => (
          <li key={index}>{display(relation.kind || 'Relationship')}{relation.status && ` · ${display(relation.status)}`}
            {relation.target && <> · {byId.has(relation.target)
              ? <button className="quiet" type="button" onClick={() => select(relation.target!)}>{byId.get(relation.target)!.statement}</button>
              : <code>{relation.target}</code>}</>}</li>
        ))}</ul></details>}
        <DeferredDetails className="knowledge-evidence" summary={<summary>Original evidence ({item.evidence.length})</summary>}>{() => <>
          {!item.evidence.length && <p>No recorded evidence excerpts.</p>}
          {item.evidence.map((evidence, index) => {
            const source = sources.get(evidence.source_id);
            const href = historical ? null : knowledgeSourceHref(snapshot.project_id, source, current);
            return <article key={`${evidence.source_id}:${index}`}>
              <strong>{evidence.label || source?.label || evidence.source_id}</strong>
              <p><small>Original source — {evidence.role}{evidence.order !== undefined && ` · source order ${evidence.order}`}</small></p>
              <blockquote>{evidence.excerpt}</blockquote>
              <details><summary>Reference metadata</summary><dl className="knowledge-fields">
                <dt>Source ID</dt><dd><code>{evidence.source_id}</code></dd>
                {evidence.conversation_id && <><dt>Conversation</dt><dd><code>{evidence.conversation_id}</code></dd></>}
                {source?.turn_id && <><dt>Turn</dt><dd><code>{source.turn_id}</code></dd></>}
                {evidence.timestamp && <><dt>Source timestamp</dt><dd>{evidence.timestamp}</dd></>}
              </dl></details>
              {href ? <a href={href}>Open original conversation</a>
                : <small>{historical ? 'Current source availability has not been checked.'
                  : 'Original navigation unavailable. The saved excerpt remains available.'}</small>}
            </article>;
          })}
        </>}</DeferredDetails>
      </div>}</DeferredDetails>;
  }
  return <div className="knowledge-items">
    <div className="knowledge-filters knowledge-primary-filters">
      <label>Search knowledge<input type="search" value={query} onChange={event => { setQuery(event.target.value); setLimits({}); }} /></label>
      <label>Design status<select aria-label="Design status" value={status} onChange={(event) => { setStatus(event.target.value); setLimits({}); }}><option value="">All statuses</option>
        {statuses.filter((value) => snapshot.items.some((item) => item.design_status === value)).map((value) => <option key={value} value={value}>{display(value)}</option>)}
      </select></label>
      <TopicPicker choices={topics} selected={topic} search={tagSearch} ariaLabel="Knowledge tags"
        onSearch={value => { setTagSearch(value); setLimits({}); }}
        onSelect={id => { setTopic(id); setLimits({}); }} />
    </div>
    <details className="knowledge-advanced-filters">
      <summary>More filters{(category || scope) && ` (${[category, scope].filter(Boolean).length} active)`}</summary>
      <div className="knowledge-filters">
        <label>Category<select aria-label="Category" value={category} onChange={(event) => { setCategory(event.target.value); setLimits({}); }}><option value="">All categories</option>
          {[...new Set(snapshot.items.map((item) => item.category))].sort().map((value) => <option key={value} value={value}>{display(value)}</option>)}
        </select></label>
        <label>Scope<input type="search" aria-label="Scope" list={`${instanceId}-scope-options`}
          placeholder="Search scopes…" value={scope} onChange={(event) => { setScope(event.target.value); setLimits({}); }} />
          <datalist id={`${instanceId}-scope-options`}>
            {[...new Set(snapshot.items.map((item) => item.scope))].filter(Boolean).sort().map((value) => <option key={value} value={value} />)}
          </datalist>
        </label>
      </div>
      <p className="muted">Type a word such as CDK2 to match all scopes containing it, or choose a suggested scope.</p>
    </details>
    <div className="knowledge-filter-footer">
      <p role="status">{items.length} of {snapshot.items.length} knowledge items</p>
      {(query || status || category || scope || tagSearch || topic !== ALL_TOPICS) &&
        <button className="quiet" type="button" onClick={reset}>Reset filters</button>}
    </div>
    {selected && !byId.has(selected) && <p role="status">The selected knowledge item is unavailable in this snapshot.</p>}
    {!items.length && <p>{snapshot.items.length ? 'No items match these filters.' : 'This snapshot contains no knowledge items.'}</p>}
    {query.trim() && <KnowledgeHistorySearch key={JSON.stringify([identity, query.trim()])}
      projectId={snapshot.project_id} query={query.trim()} />}
    {statuses.map((value) => {
      const group = items.filter((item) => item.design_status === value).sort((a, b) => a.id.localeCompare(b.id));
      if (!group.length) return null;
      const limit = limits[value] || 10;
      const passages = knowledgePassageGroups(snapshot, group, availability);
      const shown = visibleKnowledgeGroups(passages, limit, selected);
      return <section className="knowledge-group" key={value}>
        <h3>{display(value)} ({group.length})</h3>
        {shown.map(({ primary, related }) => <div key={primary.id}>
          {itemDetails(primary)}
          {!!related.length && <DeferredDetails className="knowledge-related"
            openKey={related.some(item => item.id === selected) ? `${identity}:${selected}:${selectionRequest}` : undefined}
            summary={<summary>Interpretations from the same passage ({related.length + 1} records)</summary>}>{() => <>
            <p className="muted">These records cite the same user passage. Their statements and evidence remain separate.</p>
            {visibleRelatedKnowledge(related, relatedLimits[primary.id] || 10, selected).map(itemDetails)}
            {related.length > visibleRelatedKnowledge(related, relatedLimits[primary.id] || 10, selected).length
              && <button type="button" className="secondary" onClick={() => setRelatedLimits(previous => ({
                ...previous, [primary.id]: (previous[primary.id] || 10) + 10,
              }))}>Show more related records</button>}
          </>}</DeferredDetails>}
        </div>)}
        {passages.length > shown.length && <button className="secondary" type="button"
          onClick={() => setLimits((previous) => ({ ...previous, [value]: limit + 10 }))}>Show more {display(value).toLowerCase()} items</button>}
      </section>;
    })}
  </div>;
}
