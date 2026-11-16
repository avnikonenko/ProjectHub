import { AIEmphasis } from './AIEmphasis';
import { useEffect, useId, useRef, useState } from 'react';
import { evidenceAvailability, filterKnowledge, knowledgePassageGroups, visibleKnowledgeGroups, knowledgeSourceHref, statusOrder,
  type KnowledgeItem, type KnowledgeSnapshot } from './knowledge';
import { ALL_TOPICS, filterTopicItems, topicChoices } from './topicGroups';
import { TopicPicker } from './TopicPicker';
import './knowledge.css';

const display = (value: string) => value.replaceAll('_', ' ');
export function KnowledgeItems({ snapshot, availability = {}, selectedItemId, onSelectItem }: {
  snapshot: KnowledgeSnapshot; availability?: Record<string, string>;
  selectedItemId?: string; onSelectItem?: (id: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [topic, setTopic] = useState(ALL_TOPICS);
  const [tagSearch, setTagSearch] = useState('');
  const [status, setStatus] = useState('');
  const [category, setCategory] = useState('');
  const [scope, setScope] = useState('');
  const [selected, setSelected] = useState(selectedItemId);
  const [selectionRequest, setSelectionRequest] = useState(0);
  const [limits, setLimits] = useState<Record<string, number>>({});
  const instanceId = useId();
  const selectedRef = useRef<HTMLDetailsElement | null>(null);
  const identity = `${snapshot.project_id}:${snapshot.publication_id || snapshot.generated_at || ''}`;
  const reset = () => { setQuery(''); setTagSearch(''); setTopic(ALL_TOPICS); setStatus(''); setCategory(''); setScope(''); setLimits({}); };
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
  const items = filterTopicItems(filterKnowledge(snapshot.items, query, status, category, scope, snapshot.sources),
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
    return <details key={item.id} className="knowledge-item" ref={item.id === selected ? selectedRef : undefined}
      id={`${instanceId}-knowledge-item-${encodeURIComponent(item.id)}`} open={item.id === selected || undefined}>
      <summary><span className="knowledge-item-title"><AIEmphasis text={item.statement} /></span>
        <small className="knowledge-item-meta"><span>{display(item.design_status)}</span><span>{item.scope}</span>
          <span>{item.evidence.length} {item.evidence.length === 1 ? 'reference' : 'references'}</span>
          {item.reconciliation_state === 'unresolved' && <span>Needs context review</span>}
        </small>
      </summary>
      <div className="knowledge-item-body">
        <p className="muted">Extracted interpretation</p>
        <dl className="knowledge-fields">
          <dt>Design status</dt><dd>{display(item.design_status)}</dd>
          <dt>Category</dt><dd>{display(item.category)}</dd>
          <dt>Scope</dt><dd>{item.scope}</dd>
          {item.rationale && <><dt>Rationale</dt><dd><AIEmphasis text={item.rationale} /></dd></>}
          {item.alternatives && <><dt>Alternatives</dt><dd>{item.alternatives}</dd></>}
          {item.limitations && <><dt>Limitations</dt><dd>{item.limitations}</dd></>}
          <dt>Current evidence</dt><dd>{display(current)}{current !== 'valid' && ' — historical design status is preserved.'}</dd>
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
        <details className="knowledge-evidence"><summary>Original evidence ({item.evidence.length})</summary>
          {!item.evidence.length && <p>No recorded evidence excerpts.</p>}
          {item.evidence.map((evidence, index) => {
            const source = sources.get(evidence.source_id);
            const href = knowledgeSourceHref(snapshot.project_id, source, current);
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
                : <small>Original navigation unavailable; the saved excerpt remains available.</small>}
            </article>;
          })}
        </details>
      </div>
    </details>;
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
        <label>Scope<select aria-label="Scope" value={scope} onChange={(event) => { setScope(event.target.value); setLimits({}); }}><option value="">All scopes</option>
          {[...new Set(snapshot.items.map((item) => item.scope))].sort().map((value) => <option key={value} value={value}>{value}</option>)}
        </select></label>
      </div>
    </details>
    <div className="knowledge-filter-footer">
      <p role="status">{items.length} of {snapshot.items.length} knowledge items</p>
      {(query || status || category || scope || tagSearch || topic !== ALL_TOPICS) &&
        <button className="quiet" type="button" onClick={reset}>Reset filters</button>}
    </div>
    {selected && !byId.has(selected) && <p role="status">The selected knowledge item is unavailable in this snapshot.</p>}
    {!items.length && <p>{snapshot.items.length ? 'No items match these filters.' : 'This snapshot contains no knowledge items.'}</p>}
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
          {!!related.length && <details className="knowledge-related" open={related.some(item => item.id === selected) || undefined}>
            <summary>Interpretations from the same passage ({related.length + 1} records)</summary>
            <p className="muted">These records cite the same user passage. Their statements and evidence remain separate.</p>
            {related.map(itemDetails)}
          </details>}
        </div>)}
        {passages.length > shown.length && <button className="secondary" type="button"
          onClick={() => setLimits((previous) => ({ ...previous, [value]: limit + 10 }))}>Show more {display(value).toLowerCase()} items</button>}
      </section>;
    })}
    <details className="knowledge-coverage"><summary>Knowledge coverage</summary>
      <p>Knowledge is a derived interpretation. Missing extracted information does not establish that it was never discussed.</p>
      {Object.entries(snapshot.coverage || {}).map(([key, entries]) => <details key={key}><summary>{display(key)} ({entries.length})</summary>
        <ul>{entries.map((entry, index) => <li key={index}>{entry}</li>)}</ul>
      </details>)}
      {snapshot.extraction_version && <p>Extraction version <code>{snapshot.extraction_version}</code></p>}
    </details>
  </div>;
}
