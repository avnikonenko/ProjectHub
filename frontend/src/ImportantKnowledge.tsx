import { useEffect, useState } from 'react';
import { api } from './api';
import { importantKnowledge, knowledgeHref, matchesPublication, parseKnowledgeResponse, type KnowledgeResponse } from './knowledge';

export function ImportantKnowledge({ base, publicationId, visible }: {
  base: string; publicationId?: string; visible: boolean;
}) {
  const projectId = Number(base.split('/')[2]);
  const [loaded, setLoaded] = useState<{ base: string; publicationId: string; data?: KnowledgeResponse; error?: string } | null>(null);
  useEffect(() => {
    if (!visible || !publicationId) return;
    const controller = new AbortController();
    api<unknown>(`${base}/codex/knowledge`, { signal: controller.signal })
      .then(raw => {
        const data = parseKnowledgeResponse(raw, projectId);
        if (!controller.signal.aborted) setLoaded({ base, publicationId, data });
      })
      .catch(error => {
        if (!controller.signal.aborted) setLoaded({ base, publicationId, error: error.message });
      });
    return () => controller.abort();
  }, [base, projectId, publicationId, visible]);
  if (!publicationId) return null;
  const current = loaded?.base === base && loaded.publicationId === publicationId ? loaded : null;
  const snapshot = current?.data?.snapshot;
  const matching = matchesPublication(snapshot, projectId, publicationId);
  const items = snapshot && matching ? importantKnowledge(snapshot, current?.data?.current_evidence_availability) : [];
  return <section className="card important-knowledge" aria-label="Important choices">
    <h2>Important choices</h2>
    {!current && <p role="status">Loading associated knowledge…</p>}
    {current?.error && <p role="alert">Could not load associated knowledge: {current.error}</p>}
    {current?.data && !matching && <p>The published knowledge does not match this overview. Open Knowledge to inspect the current snapshot separately.</p>}
    {matching && <>
      <ul>{items.map(item => <li key={item.id}>
        <small>{item.design_status.replaceAll('_', ' ')}</small>{' · '}
        <a href={knowledgeHref({ projectId, publicationId, itemId: item.id })}>{item.statement}</a>
        <div className="muted">{item.scope}</div>
      </li>)}</ul>
      {!items.length && <p>No supported choices selected for this overview.</p>}
    </>}
    <a href={knowledgeHref({ projectId, publicationId })}>Explore all project knowledge →</a>
  </section>;
}
