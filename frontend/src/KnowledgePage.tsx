import { TagGeneration } from './TagGeneration';
import { Feedback } from './Feedback';
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
import { activateKnowledgeResult } from './knowledgePublication';

type LoadState = { base: string; key: string; loading: boolean; data: KnowledgeResponse | null; error: string };
type PreviewState = { base: string; busy: boolean; result: KnowledgePreview | null; error: string; started: number; loading?: boolean; cancelled?: boolean };
type ActivationState = { base: string; busy: boolean; error: string; notice: string };
type Props = {
  base: string;
  generation: { model: string; effort: string };
  link?: KnowledgeLink | null;
  visible?: boolean;
  renderBrief: (brief: Brief) => ReactNode;
  onPublished?: () => void;
};

export function KnowledgePage({ base, generation, link, renderBrief, visible = true, onPublished }: Props) {
  const projectId = Number(base.split('/')[2]);
  const projectLink = link?.projectId === projectId ? link : null;
  const publicationKey = projectLink?.publicationId ?? '';
  const loadKey = JSON.stringify([base, publicationKey]);
  const [publishedTagsBusy, setPublishedTagsBusy] = useState(false);
  const [previewTagsBusy, setPreviewTagsBusy] = useState(false);
  const tagsBusy = publishedTagsBusy || previewTagsBusy;
  const [retry, setRetry] = useState(0);
  const [load, setLoad] = useState<LoadState>({ base, key: loadKey, loading: true, data: null, error: '' });
  const [preview, setPreview] = useState<PreviewState>({ base, busy: false, result: null, error: '', started: 0 });
  const [activation, setActivation] = useState<ActivationState>({ base, busy: false, error: '', notice: '' });
  const [dismissedLink, setDismissedLink] = useState('');
  const active = useRef<{ base: string; controller: AbortController } | null>(null);
  const activating = useRef<{ base: string; controller: AbortController } | null>(null);
  const recovery = useRef<AbortController | null>(null);
  const previewRequestVersion = useRef(0);
  const snapshot = load.key === loadKey ? load.data?.snapshot : null;
  const currentPreview = preview.base === base ? preview : null;
  const currentActivation = activation.base === base ? activation : null;
  const busy = !!currentPreview?.busy;
  const publishing = !!currentActivation?.busy;
  const candidate = currentPreview?.result && currentPreview.result.knowledge.publication_id !== snapshot?.publication_id
    ? currentPreview.result : null;
  const requestKey = `${base}:${projectLink?.publicationId ?? ''}:${projectLink?.itemId ?? ''}`;
  const expectedPublication = dismissedLink === requestKey ? undefined : projectLink?.publicationId;
  const mismatch = !!snapshot && !!expectedPublication
    && !matchesPublication(snapshot, projectId, expectedPublication);
  const selectedItemId = dismissedLink === requestKey ? undefined : projectLink?.itemId;

  useEffect(() => {
    setLoad(previous => ({ base, key: loadKey, loading: true,
      data: previous.key === loadKey ? previous.data : null, error: '' }));
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
        if (!controller.signal.aborted) setLoad(previous => ({ base, key: loadKey,
          loading: false, data: previous.key === loadKey ? previous.data : null, error: error.message }));
      });
    return () => controller.abort();
  }, [base, projectId, retry, visible, loadKey]);

  useEffect(() => () => {
    active.current?.controller.abort();
    active.current = null;
    activating.current?.controller.abort();
    activating.current = null;
  }, [base]);

  useEffect(() => {
    if (!visible || active.current?.base === base || activating.current?.base === base) return;
    const controller = new AbortController();
    recovery.current = controller;
    const version = ++previewRequestVersion.current;
    const canApply = () => !controller.signal.aborted && !active.current && !activating.current
      && version === previewRequestVersion.current;
    setPreview((previous) => previous.base === base ? { ...previous, loading: true }
      : { base, busy: false, result: null, error: '', started: 0, loading: true });
    void recoverKnowledgePreview(base, projectId, controller.signal, (event) => {
      if (!canApply()) return;
      setPreview((previous) => {
        const current = previous.base === base ? previous
          : { base, busy: false, result: null, error: '', started: 0 };
        if (event.type === 'saved') return { ...current, result: event.result };
        const status = event.progress;
        return { ...current, busy: status.state === 'running', loading: false,
          started: status.state === 'running' ? Date.parse(status.started_at || '') || Date.now() : 0,
          cancelled: status.state === 'cancelled',
          error: status.state === 'failed' ? status.error || 'Knowledge generation failed. The saved result is preserved.'
            : status.state === 'idle' && current.busy
              ? 'Generation is no longer running. The saved result is preserved.' : '' };
      });
    }).catch((error: Error) => {
      if (canApply()) setPreview((previous) => ({ ...previous, busy: false, started: 0, loading: false,
        error: `Could not load the saved result: ${error.message}` }));
    });
    return () => {
      controller.abort();
      if (recovery.current === controller) recovery.current = null;
    };
  }, [base, projectId, retry, visible, loadKey]);

  async function generatePreview() {
    if (active.current || activating.current || tagsBusy) return;
    previewRequestVersion.current++;
    recovery.current?.abort();
    const controller = new AbortController();
    const run = { base, controller };
    active.current = run;
    setActivation({ base, busy: false, error: '', notice: '' });
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

  async function useResult() {
    if (!candidate || active.current || activating.current || busy || tagsBusy) return;
    previewRequestVersion.current++;
    recovery.current?.abort();
    const controller = new AbortController();
    const run = { base, controller };
    activating.current = run;
    setActivation({ base, busy: true, error: '', notice: '' });
    try {
      const result = await activateKnowledgeResult(base, projectId, candidate.knowledge.publication_id!, controller.signal);
      if (!controller.signal.aborted && activating.current === run) {
        setLoad({ base, key: loadKey, loading: false, error: '', data: {
          snapshot: result.knowledge, paired_with_current_brief: true,
          current_evidence_availability: {}, coverage: result.knowledge.coverage || {},
        } });
        setActivation({ base, busy: false, error: '', notice: 'Knowledge and Overview updated.' });
        onPublished?.();
        setRetry(value => value + 1);
      }
    } catch (error) {
      if (!controller.signal.aborted && activating.current === run)
        setActivation({ base, busy: false, error: (error as Error).message, notice: '' });
    } finally {
      if (activating.current === run) activating.current = null;
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
  return <section className="knowledge-page">
    <section className="card" aria-busy={isLoading || publishing}>
      <div className="section-title">
        <div><h2>Project Knowledge</h2><small>Project #{projectId}</small></div>
        <button disabled={tagsBusy || busy || publishing || currentPreview?.loading} onClick={generatePreview}>
          {busy ? 'Generating…' : snapshot || currentPreview?.result ? 'Regenerate' : 'Generate'}
        </button>
      </div>
      <p>Explore project choices, reasons and their original evidence.</p>
      <small className="muted">Generation sends selected project evidence to OpenAI through Codex.</small>
      <ErrorBox error={error} />
      <ErrorBox error={currentActivation?.error || ''} />
      <ErrorBox error={currentPreview?.error || ''} />
      {publishing && <Feedback>Updating Knowledge and Overview…</Feedback>}
      <Feedback tone="success">{currentActivation?.notice}</Feedback>
      {currentPreview?.cancelled && <Feedback>{cancelledGenerationMessage}</Feedback>}
      {busy && <GenerationStatus key={`${base}:${currentPreview?.started}`} base={base}
        kind="knowledge" label="Knowledge generation" />}
      {(isLoading || currentPreview?.loading) && !snapshot && !candidate && <Feedback>Loading saved knowledge…</Feedback>}
      {!isLoading && !currentPreview?.loading && !error && !currentPreview?.error && !snapshot && !candidate
        && <Empty>No saved knowledge yet. Generate a result to get started.</Empty>}
      {!isLoading && !snapshot && expectedPublication
        && <Feedback>The linked knowledge version is not currently available.</Feedback>}
      {snapshot && <>
        <small>Current knowledge{snapshot.generated_at && ` · ${new Date(snapshot.generated_at).toLocaleString()}`}</small>
        <TagGeneration key={`${base}:tags:${snapshot.publication_id}`} base={base} target="knowledge"
          publicationId={snapshot.publication_id} itemIds={snapshot.items.map(item => item.id)}
          generation={generation} hasTags={!!snapshot.topic_groups?.length} disabled={busy || publishing || !snapshot.publication_id}
          onBusyChange={setPublishedTagsBusy} onRecovered={() => setRetry(value => value + 1)}
          onResult={result => setLoad(previous => previous.base === base && previous.data && previous.data.snapshot && previous.data.snapshot.publication_id === result.publication_id
            ? { ...previous, data: { ...previous.data, snapshot: { ...previous.data.snapshot,
              topic_groups: result.topic_groups, topic_grouping: result.topic_grouping } } } : previous)} />
        {mismatch ? <Feedback>
          <p>This overview references another knowledge publication. Its selected record is not being substituted with the current version.</p>
          <button className="secondary" onClick={() => setDismissedLink(requestKey)}>Explore current knowledge</button>
        </Feedback> : <>
          {!!absentItem && <Feedback>The requested item is not in this version. Other current records are shown below.</Feedback>}
          <KnowledgeItems key={`${base}:${snapshot.publication_id ?? snapshot.generated_at ?? 'published'}`}
            snapshot={snapshot} availability={load.data?.current_evidence_availability}
            selectedItemId={absentItem ? undefined : selectedItemId} />
          {!!Object.values(load.data?.coverage ?? {}).some(values => values.length > 0) && <details>
            <summary>Current source coverage</summary>
            {Object.entries(load.data?.coverage ?? {}).filter(([, values]) => values.length > 0).map(([name, values]) => <div key={name}>
              <strong>{name.replaceAll('_', ' ')}{name === 'limitations' ? '' : ` (${values.length})`}</strong>
              <ul>{values.map((value, index) => <li key={index}>{value}</li>)}</ul>
            </div>)}
          </details>}
          {!candidate && currentPreview?.result && <>
            <button className="secondary" onClick={downloadPreview}>Download JSON</button>
            <GenerationDetails diagnostics={currentPreview.result.diagnostics} />
          </>}
        </>}
      </>}
    </section>
    {candidate && <section className="card knowledge-preview" aria-label="Generated knowledge result">
      <div className="section-title">
        <div><h3>{snapshot ? 'New result' : 'Saved result'}</h3>
          <small>{new Date(candidate.brief.generated_at).toLocaleString()}</small></div>
        <button disabled={tagsBusy || busy || publishing || isLoading || currentPreview?.loading} onClick={useResult}>
          {publishing ? 'Applying…' : 'Use this result'}
        </button>
      </div>
      <p>Use this result updates Knowledge and Overview together.</p>
      <button className="secondary" onClick={downloadPreview}>Download JSON</button>
      <GenerationDetails diagnostics={candidate.diagnostics} />
      {renderBrief(candidate.brief)}
      <h3>Knowledge</h3>
      <TagGeneration key={`${base}:preview-tags:${candidate.knowledge.publication_id}`} base={base} target="knowledge_preview"
        publicationId={candidate.knowledge.publication_id} itemIds={candidate.knowledge.items.map(item => item.id)}
        generation={generation} hasTags={!!candidate.knowledge.topic_groups?.length} disabled={busy || publishing || !candidate.knowledge.publication_id}
        onBusyChange={setPreviewTagsBusy} onRecovered={() => setRetry(value => value + 1)}
        onResult={result => setPreview(previous => previous.base === base && previous.result && previous.result.knowledge.publication_id === result.publication_id
          ? { ...previous, result: { ...previous.result, knowledge: { ...previous.result.knowledge,
            topic_groups: result.topic_groups, topic_grouping: result.topic_grouping } } } : previous)} />
      <KnowledgeItems key={`${base}:preview:${candidate.knowledge.publication_id}`} snapshot={candidate.knowledge} />
    </section>}
  </section>;
}
