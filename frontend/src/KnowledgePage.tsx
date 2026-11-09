import { useEffect, useRef, useState, type ReactNode } from 'react';
import { api } from './api';
import { KnowledgeItems } from './KnowledgeItems';
import {
  matchesPublication, parseKnowledgeResponse, parseKnowledgePreview,
  type KnowledgeLink, type KnowledgeResponse, type KnowledgeSnapshot,
} from './knowledge';
import { Empty, ErrorBox } from './ui';
import type { Brief } from './BriefContent';

type Preview = { knowledge: KnowledgeSnapshot; brief: Brief };
type LoadState = { base: string; key: string; loading: boolean; data: KnowledgeResponse | null; error: string };
type PreviewState = { base: string; busy: boolean; result: Preview | null; error: string; started: number };
type Props = {
  base: string;
  generation: { model: string; effort: string };
  link?: KnowledgeLink | null;
  visible?: boolean;
  renderBrief: (brief: Brief) => ReactNode;
};


export function KnowledgePage({ base, generation, link, renderBrief, visible = true }: Props) {
  const projectId = Number(base.split('/')[2]);
  const projectLink = link?.projectId === projectId ? link : null;
  const publicationKey = projectLink?.publicationId ?? '';
  const loadKey = JSON.stringify([base, publicationKey]);
  const [retry, setRetry] = useState(0);
  const [load, setLoad] = useState<LoadState>({ base, key: loadKey, loading: true, data: null, error: '' });
  const [preview, setPreview] = useState<PreviewState>({ base, busy: false, result: null, error: '', started: 0 });
  const [dismissedLink, setDismissedLink] = useState('');
  const [progress, setProgress] = useState({ base, stage: '', error: '' });
  const [elapsed, setElapsed] = useState(0);
  const active = useRef<{ base: string; controller: AbortController } | null>(null);
  const snapshot = load.key === loadKey ? load.data?.snapshot : null;
  const currentPreview = preview.base === base ? preview : null;
  const busy = !!currentPreview?.busy;
  const requestKey = `${base}:${projectLink?.publicationId ?? ''}:${projectLink?.itemId ?? ''}`;
  const expectedPublication = dismissedLink === requestKey ? undefined : projectLink?.publicationId;
  const mismatch = !!snapshot && !!expectedPublication
    && !matchesPublication(snapshot, projectId, expectedPublication);
  const selectedItemId = dismissedLink === requestKey ? undefined : projectLink?.itemId;

  useEffect(() => {
    setLoad({ base, key: loadKey, loading: true, data: null, error: '' });
    if (!visible) return;
    const controller = new AbortController();
    api<unknown>(`${base}/codex/knowledge`, { signal: controller.signal })
      .then((raw) => {
        if (!controller.signal.aborted) {
          const data = parseKnowledgeResponse(raw, projectId);
          setLoad({ base, key: loadKey, loading: false, data, error: '' });
        }
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted) setLoad({ base, key: loadKey, loading: false, data: null, error: error.message });
      });
    return () => controller.abort();
  }, [base, projectId, retry, visible, loadKey]);

  useEffect(() => () => {
    active.current?.controller.abort();
    active.current = null;
  }, [base]);

  useEffect(() => {
    if (!busy) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = currentPreview?.started ?? Date.now();
    setProgress({ base, stage: 'Preparing source-linked knowledge', error: '' });
    setElapsed(0);
    const clock = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    async function poll() {
      try {
        const status = await api<{ state: string; stage?: string }>(
          `${base}/codex/generation/status?kind=knowledge`, { signal: controller.signal });
        if (!controller.signal.aborted) setProgress((previous) => ({ base,
          stage: status.state === 'running' ? status.stage || previous.stage : previous.stage, error: '' }));
      } catch (error) {
        if (!controller.signal.aborted) setProgress((previous) => ({ ...previous,
          error: `Progress unavailable: ${(error as Error).message}` }));
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 1500);
    }
    void poll();
    return () => { controller.abort(); clearInterval(clock); if (timer) clearTimeout(timer); };
  }, [base, busy, currentPreview?.started]);

  async function generatePreview() {
    if (active.current) return;
    const controller = new AbortController();
    const run = { base, controller };
    active.current = run;
    setPreview({ base, busy: true, result: null, error: '', started: Date.now() });
    try {
      const raw = await api<unknown>(`${base}/codex/knowledge/brief`, {
        method: 'POST', signal: controller.signal,
        body: JSON.stringify({ confirmed: true, publish: false,
          model: generation.model || null, effort: generation.effort || null }),
      });
      if (!controller.signal.aborted && active.current === run) {
        const result = parseKnowledgePreview(raw, projectId);
        setPreview({ base, busy: false, result, error: '', started: 0 });
      }
    } catch (error) {
      if (!controller.signal.aborted && active.current === run) setPreview({ base, busy: false,
        result: null, error: (error as Error).message, started: 0 });
    } finally {
      if (active.current === run) active.current = null;
    }
  }

  const isLoading = load.key !== loadKey || load.loading;
  const error = load.key === loadKey ? load.error : '';
  const absentItem = snapshot && selectedItemId && !snapshot.items.some((item) => item.id === selectedItemId);
  return (
    <section className="knowledge-page">
      <section className="card" aria-busy={isLoading}>
        <div className="section-title">
          <div><h2>Project Knowledge</h2><small>Published knowledge · Project #{projectId}</small></div>
          <button className="secondary" disabled={isLoading} onClick={() => setRetry((value) => value + 1)}>Reload knowledge</button>
        </div>
        <p>Explore extracted interpretations and their original evidence.</p>
        <ErrorBox error={error} />
        {isLoading && <Empty>Loading published project knowledge…</Empty>}
        {!isLoading && !error && !snapshot && <Empty>
          {expectedPublication ? 'The linked overview refers to a knowledge publication that is not currently available. ' : 'No published knowledge yet. '}
          The existing project overview is unchanged. You can inspect an unpublished preview below.
        </Empty>}
        {!isLoading && snapshot && (
          <>
            <small>{snapshot.generated_at ? `Generated ${new Date(snapshot.generated_at).toLocaleString()} · ` : ''}
              {snapshot.publication_id ? `Publication ${snapshot.publication_id}` : 'Publication identity unavailable'}</small>
            {mismatch ? <div role="status">
              <p>This overview references another knowledge publication. Its selected record is not being substituted with the current version.</p>
              <button className="secondary" onClick={() => setDismissedLink(requestKey)}>Explore current published knowledge</button>
            </div> : <>
              {!!absentItem && <p role="status">The requested knowledge item is not in this publication. Other current records are shown below.</p>}
              <KnowledgeItems key={`${base}:${snapshot.publication_id ?? snapshot.generated_at ?? 'published'}`}
                snapshot={snapshot} availability={load.data?.current_evidence_availability}
                selectedItemId={absentItem ? undefined : selectedItemId} />
              {!!Object.values(load.data?.coverage ?? {}).some((values) => values.length > 0) && (
                <details>
                  <summary>Current source coverage</summary>
                  {Object.entries(load.data?.coverage ?? {}).filter(([, values]) => values.length > 0).map(([name, values]) => (
                    <div key={name}>
                      <strong>{name.replaceAll('_', ' ')}{name === 'limitations' ? '' : ` (${values.length})`}</strong>
                      <ul>{values.map((value, index) => <li key={index}>{value}</li>)}</ul>
                    </div>
                  ))}
                </details>
              )}
            </>}
          </>
        )}
      </section>
      <details className="card knowledge-preview">
        <summary>Try an unpublished knowledge preview</summary>
        <p>This opt-in action sends selected project conversations and evidence to OpenAI through Codex. It does not replace published knowledge or the saved overview.</p>
        <button disabled={busy} onClick={generatePreview}>{busy ? 'Generating preview…' : 'Generate unpublished preview'}</button>
        {busy && <div className="generation-progress" role="status" aria-live="polite">
          <p>{progress.base === base ? progress.stage : 'Preparing source-linked knowledge'} · Elapsed {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')}</p>
          {progress.base === base && progress.error && <small>{progress.error}</small>}
        </div>}
        <ErrorBox error={currentPreview?.error ?? ''} />
        {currentPreview?.result && <section aria-label="Unpublished knowledge preview">
          <h3>Unpublished preview</h3>
          <p>This result is separate from published project knowledge.</p>
          {renderBrief(currentPreview.result.brief)}
          <h3>Preview knowledge</h3>
          <KnowledgeItems key={`${base}:preview:${currentPreview.result.knowledge.publication_id}`}
            snapshot={currentPreview.result.knowledge} />
        </section>}
      </details>
    </section>
  );
}
