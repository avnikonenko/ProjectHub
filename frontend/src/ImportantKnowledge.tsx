import { AIEmphasis } from './AIEmphasis';
import { Feedback } from './Feedback';
import { useEffect, useState } from 'react';
import { readPublishedKnowledge, subscribePublishedKnowledge } from './publishedKnowledge';
import { importantKnowledge, knowledgeHref, knowledgePassageGroups, matchesPublication, type KnowledgeResponse } from './knowledge';

export function ImportantKnowledge({ base, publicationId, visible }: {
  base: string; publicationId?: string; visible: boolean;
}) {
  const projectId = Number(base.split('/')[2]);
  const [loaded, setLoaded] = useState<{ base: string; publicationId: string; data?: KnowledgeResponse; error?: string; loading: boolean } | null>(null);
  useEffect(() => {
    if (!publicationId) return;
    return subscribePublishedKnowledge(base, state => {
      setLoaded({ base, publicationId, data: state.data ?? undefined, error: state.error, loading: state.loading });
    });
  }, [base, publicationId]);
  useEffect(() => {
    if (visible && publicationId) void readPublishedKnowledge(base, { publicationId });
  }, [base, publicationId, visible]);
  if (!publicationId) return null;
  const current = loaded?.base === base && loaded.publicationId === publicationId ? loaded : null;
  const snapshot = current?.data?.snapshot;
  const matching = matchesPublication(snapshot, projectId, publicationId);
  const items = snapshot && matching ? importantKnowledge(snapshot, current?.data?.current_evidence_availability) : [];
  const passageCounts = new Map(snapshot && matching
    ? knowledgePassageGroups(snapshot, snapshot.items, current?.data?.current_evidence_availability)
      .map(group => [group.primary.id, group.related.length + 1]) : []);
  return <section className="card important-knowledge" aria-label="Important choices">
    <h2>Important choices</h2>
    {(!current || (current.loading && !snapshot)) && <Feedback>Loading associated knowledge…</Feedback>}
    {current?.error && <Feedback tone="error">Could not load associated knowledge: {current.error}</Feedback>}
    {current?.data && !matching && <p>The published knowledge does not match this overview. Open Knowledge to inspect the current snapshot separately.</p>}
    {matching && <>
      <ul>{items.map(item => <li key={item.id}>
        <small>{item.design_status.replaceAll('_', ' ')}</small>{' · '}
        <a href={knowledgeHref({ projectId, publicationId, itemId: item.id })}><AIEmphasis text={item.statement} /></a>
        <div className="muted">{item.scope}</div>
        {(passageCounts.get(item.id) || 0) > 1 && <small>{passageCounts.get(item.id)} interpretations from the same passage</small>}
      </li>)}</ul>
      {!items.length && <p>No supported choices selected for this overview.</p>}
    </>}
    <a href={knowledgeHref({ projectId, publicationId })}>Explore all project knowledge →</a>
  </section>;
}
