import { GenerationDetails } from './GenerationDetails';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { api } from './api';
import { KnowledgeItems } from './KnowledgeItems';
import {
  matchesPublication, parseKnowledgeResponse, parseKnowledgePreview,
  type KnowledgeLink, type KnowledgeResponse,
} from './knowledge';
import { Empty, ErrorBox } from './ui';
import { GenerationStatus, isGenerationCancelled, cancelledGenerationMessage } from './GenerationStatus';
import type { Brief } from './BriefContent';
import { knowledgePreviewExport, recoverKnowledgePreview, type KnowledgePreview } from './knowledgePreviewRecovery';

type LoadState = { base: string; key: string; loading: boolean; data: KnowledgeResponse | null; error: string };
type PreviewState = { base: string; busy: boolean; result: KnowledgePreview | null; error: string; started: number; loading?: boolean; cancelled?: boolean };
type ReloadState = { key: string; attempt: number; published: boolean; preview: boolean; failed: boolean };
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
  const [reload, setReload] = useState<ReloadState | null>(null);
  const [load, setLoad] = useState<LoadState>({ base, key: loadKey, loading: true, data: null, error: '' });
  const [preview, setPreview] = useState<PreviewState>({ base, busy: false, result: null, error: '', started: 0 });
  const [dismissedLink, setDismissedLink] = useState('');
  const active = useRef<{ base: string; controller: AbortController } | null>(null);
  const recovery = useRef<AbortController | null>(null);
  const previewRequestVersion = useRef(0);
  const previewPanel = useRef<HTMLDetailsElement | null>(null);
  const snapshot = load.key === loadKey ? load.data?.snapshot : null;
  const currentPreview = preview.base === base ? preview : null;
  const busy = !!currentPreview?.busy;
  const requestKey = `${base}:${projectLink?.publicationId ?? ''}:${projectLink?.itemId ?? ''}`;
  const expectedPublication = dismissedLink === requestKey ? undefined : projectLink?.publicationId;
  const mismatch = !!snapshot && !!expectedPublication
    && !matchesPublication(snapshot, projectId, expectedPublication);
  const selectedItemId = dismissedLink === requestKey ? undefined : projectLink?.itemId;
  const currentReload = reload?.key === loadKey && reload.attempt === retry ? reload : null;
  const reloadPending = !!currentReload && !currentReload.failed && (!currentReload.published || !currentReload.preview);
  const reloadComplete = !!currentReload && !currentReload.failed && currentReload.published && currentReload.preview;

  function markReload(part: 'published' | 'preview', failed = false) {
    setReload((previous) => previous?.key === loadKey && previous.attempt === retry
      ? { ...previous, [part]: true, failed: previous.failed || failed } : previous);
  }

  function reloadSaved() {
    if (busy || reloadPending) return;
    setReload({ key: loadKey, attempt: retry + 1, published: false, preview: false, failed: false });
    setRetry((value) => value + 1);
  }

  useEffect(() => { setReload(null); }, [loadKey]);

  useEffect(() => {
    if (previewPanel.current && ((reloadComplete && !snapshot && currentPreview?.result)
      || (currentReload?.failed && currentPreview?.error)))
      previewPanel.current.open = true;
  }, [reloadComplete, currentReload?.failed, snapshot, currentPreview?.result, currentPreview?.error]);

  useEffect(() => {
    setLoad({ base, key: loadKey, loading: true, data: null, error: '' });
    if (!visible) return;
    const controller = new AbortController();
    api<unknown>(`${base}/codex/knowledge`, { signal: controller.signal })
      .then((raw) => {
        if (!controller.signal.aborted) {
          const data = parseKnowledgeResponse(raw, projectId);
          setLoad({ base, key: loadKey, loading: false, data, error: '' });
          markReload('published');
        }
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted) {
          setLoad({ base, key: loadKey, loading: false, data: null, error: error.message });
          markReload('published', true);
        }
      });
    return () => controller.abort();
  }, [base, projectId, retry, visible, loadKey]);

  useEffect(() => () => {
    active.current?.controller.abort();
    active.current = null;
  }, [base]);

  useEffect(() => {
    if (!visible || active.current?.base === base) return;
    const controller = new AbortController();
    recovery.current = controller;
    const version = ++previewRequestVersion.current;
    const canApply = () => !controller.signal.aborted && !active.current && version === previewRequestVersion.current;
    setPreview((previous) => previous.base === base ? { ...previous, loading: true }
      : { base, busy: false, result: null, error: '', started: 0, loading: true });
    void recoverKnowledgePreview(base, projectId, controller.signal, (event) => {
      if (!canApply()) return;
      if (event.type === 'progress') markReload('preview');
      setPreview((previous) => {
        const current = previous.base === base ? previous
          : { base, busy: false, result: null, error: '', started: 0 };
        if (event.type === 'saved') return { ...current, result: event.result };
        const status = event.progress;
        return { ...current, busy: status.state === 'running', loading: false,
          started: status.state === 'running' ? Date.parse(status.started_at || '') || Date.now() : 0,
          cancelled: status.state === 'cancelled',
          error: status.state === 'failed' ? status.error || 'Knowledge generation failed. The previous preview is preserved.'
            : status.state === 'idle' && current.busy
              ? 'Generation is no longer running. The server may have restarted; the previous preview is preserved.' : '' };
      });
    }).catch((error: Error) => {
      if (canApply()) {
        markReload('preview', true);
        setPreview((previous) => ({ ...previous, busy: false, started: 0, loading: false,
          error: `Could not recover the knowledge preview: ${error.message}` }));
      }
    });
    return () => {
      controller.abort();
      if (recovery.current === controller) recovery.current = null;
    };
  }, [base, projectId, retry, visible, loadKey]);

  async function generatePreview() {
    if (active.current || reloadPending) return;
    previewRequestVersion.current++;
    recovery.current?.abort();
    const controller = new AbortController();
    const run = { base, controller };
    active.current = run;
    setPreview((previous) => ({ base, busy: true, result: previous.base === base ? previous.result : null,
      error: '', started: Date.now(), loading: false }));
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
      if (!controller.signal.aborted && active.current === run) setPreview((previous) => ({ base, busy: false,
        result: previous.base === base ? previous.result : null,
        error: isGenerationCancelled(error) ? '' : (error as Error).message,
        started: 0, cancelled: isGenerationCancelled(error) }));
    } finally {
      if (active.current === run) active.current = null;
    }
  }

  function downloadPreview() {
    if (!currentPreview?.result) return;
    try {
      const exported = knowledgePreviewExport(currentPreview.result, projectId);
      const url = URL.createObjectURL(new Blob([exported.text], { type: 'application/json;charset=utf-8' }));
      const anchor = document.createElement('a');
      anchor.href = url; anchor.download = exported.filename;
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      setPreview((previous) => ({ ...previous, error: (error as Error).message }));
    }
  }

  const isLoading = load.key !== loadKey || load.loading;
  const error = load.key === loadKey ? load.error : '';
  const absentItem = snapshot && selectedItemId && !snapshot.items.some((item) => item.id === selectedItemId);
  return (
    <section className="knowledge-page">
      <section className="card" aria-busy={isLoading || reloadPending}>
        <div className="section-title">
          <div><h2>Project Knowledge</h2><small>{snapshot ? 'Published knowledge · ' : ''}Project #{projectId}</small></div>
          <button className="secondary" disabled={isLoading || reloadPending || busy} onClick={reloadSaved}>
            {reloadPending ? 'Reloading…' : 'Reload saved results'}
          </button>
        </div>
        <p>Explore extracted interpretations and their original evidence.</p>
        <ErrorBox error={error} />
        {reloadComplete && <p role="status">Saved results reloaded. Reloading does not generate or publish knowledge.</p>}
        {currentReload?.failed && <p role="status">Could not reload all saved results. See the error details.</p>}
        {isLoading && <Empty>Loading published project knowledge…</Empty>}
        {!isLoading && !error && !snapshot && <Empty>
          {expectedPublication ? 'The linked overview refers to a knowledge publication that is not currently available. '
            : currentPreview?.result ? 'Generated knowledge is saved as a preview below. '
              : currentPreview?.loading ? 'Checking for saved knowledge. '
                : currentPreview?.error ? 'Saved knowledge could not be loaded. '
                  : 'No generated knowledge is available yet. '}
          {currentPreview?.result ? 'It has not replaced the saved project overview.'
            : currentPreview?.error ? 'The saved project overview is unchanged.'
              : 'Open the preview section below to generate it. The saved project overview is unchanged.'}
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
      <details className="card knowledge-preview" ref={previewPanel}>
        <summary>Try an unpublished knowledge preview</summary>
        <p>This opt-in action sends selected project conversations and evidence to OpenAI through Codex. It does not replace published knowledge or the saved overview.</p>
        <button disabled={busy || reloadPending || currentPreview?.loading} onClick={generatePreview}>{busy ? 'Generating preview…' : 'Generate unpublished preview'}</button>
        {currentPreview?.loading && <p role="status">Checking the saved preview and generation status…</p>}
        {busy && <GenerationStatus key={`${base}:${currentPreview?.started}`} base={base}
          kind="knowledge" label="Knowledge preview" />}
        {currentPreview?.cancelled && <p role="status">{cancelledGenerationMessage}</p>}
        <ErrorBox error={currentPreview?.error ?? ''} />
        {currentPreview?.result && <section aria-label="Unpublished knowledge preview">
          <h3>Unpublished preview</h3>
          <small>Generated {new Date(currentPreview.result.brief.generated_at).toLocaleString()} · Unpublished</small>
          <p>This saved preview is separate from published project knowledge.
            {busy && ' The previous successful preview remains available while the new generation runs.'}</p>
          <button className="secondary" onClick={downloadPreview}>Download preview JSON</button>
          <GenerationDetails diagnostics={currentPreview.result.diagnostics} />
          {renderBrief(currentPreview.result.brief)}
          <h3>Preview knowledge</h3>
          <KnowledgeItems key={`${base}:preview:${currentPreview.result.knowledge.publication_id}`}
            snapshot={currentPreview.result.knowledge} />
        </section>}
      </details>
    </section>
  );
}
