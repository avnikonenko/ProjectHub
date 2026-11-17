import { TagGeneration } from './TagGeneration';
import { Feedback } from './Feedback';
import { CoverageDetails } from './CoverageDetails';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { api } from './api';
import { KnowledgeItems } from './KnowledgeItems';
import {
  matchesPublication, parseKnowledgePreview,
  type KnowledgeLink, type KnowledgeResponse, type KnowledgeSnapshot,
} from './knowledge';
import { Empty, ErrorBox } from './ui';
import { GenerationStatus, isGenerationCancelled, cancelledGenerationMessage } from './GenerationStatus';
import type { Brief } from './BriefContent';
import { knowledgePreviewExport, recoverKnowledgePreview, type KnowledgePreview } from './knowledgePreviewRecovery';
import { activateKnowledgeResult } from './knowledgePublication';
import { readPublishedKnowledge, subscribePublishedKnowledge, updatePublishedKnowledge } from './publishedKnowledge';

type LoadState = { base: string; key: string; loading: boolean; data: KnowledgeResponse | null; error: string };
type PreviewState = { base: string; busy: boolean; result: KnowledgePreview | null; error: string; started: number; loading?: boolean; cancelled?: boolean };
type ActivationState = { base: string; busy: boolean; error: string; notice: string };
type Props = {
  base: string;
  generation: { model: string; effort: string };
  link?: KnowledgeLink | null;
  visible?: boolean;
  renderBrief: (brief: Brief, context?: 'published' | 'preview') => ReactNode;
  onPublished?: () => void;
  showRecords?: boolean;
  externalBusy?: boolean;
  refreshRequest?: number;
  onBusyChange?: (state: { busy: boolean; running: boolean }) => void;
};

export function PublishedKnowledgeSummary({ snapshot, renderBrief }: {
  snapshot: KnowledgeSnapshot;
  renderBrief: (brief: Brief, context?: 'published' | 'preview') => ReactNode;
}) {
  if (!snapshot.summary && !snapshot.summary_error) return null;
  const summary = snapshot.summary && matchesPublication(snapshot, snapshot.project_id,
    snapshot.summary.project_state?.publication_id) ? snapshot.summary : null;
  const summaryError = snapshot.summary_error || (snapshot.summary && !summary
    ? 'The saved Knowledge Summary does not match this Knowledge publication. Records remain available.' : '');
  return <section className="card knowledge-summary" aria-label="Saved Knowledge Summary">
    <div className="section-title"><div><h2>Knowledge Summary</h2>
      <small>Saved separately from Project Overview</small></div></div>
    <p>A detailed perspective on extracted decisions, reasoning and uncertainties.</p>
    <ErrorBox error={summaryError} />
    {summary && renderBrief(summary, 'published')}
  </section>;
}

export function KnowledgePage({ base, generation, link, renderBrief, visible = true, onPublished, showRecords = true, externalBusy = false, refreshRequest = 0, onBusyChange }: Props) {
  const projectId = Number(base.split('/')[2]);
  const projectLink = link?.projectId === projectId ? link : null;
  const publicationKey = projectLink?.publicationId ?? '';
  const loadKey = JSON.stringify([base, publicationKey]);
  const [publishedTagsBusy, setPublishedTagsBusy] = useState(false);
  const [previewTagsBusy, setPreviewTagsBusy] = useState(false);
  const tagsBusy = publishedTagsBusy || previewTagsBusy;
  const [previewView, setPreviewView] = useState<'summary' | 'knowledge'>('summary');
  const lastRequested = useRef(refreshRequest);
  const [retry, setRetry] = useState(0);
  const [recoveryAttempt, setRecoveryAttempt] = useState(0);
  const [load, setLoad] = useState<LoadState>({ base, key: loadKey, loading: true, data: null, error: '' });
  const [preview, setPreview] = useState<PreviewState>({ base, busy: false, result: null, error: '', started: 0 });
  const [activation, setActivation] = useState<ActivationState>({ base, busy: false, error: '', notice: '' });
  const [dismissedLink, setDismissedLink] = useState('');
  const active = useRef<{ base: string; controller: AbortController } | null>(null);
  const activating = useRef<{ base: string; controller: AbortController } | null>(null);
  const recovery = useRef<AbortController | null>(null);
  const previewRequestVersion = useRef(0);
  const lastProgressStartedAt = useRef<string | undefined>(undefined);
  const recoveryHandoff = useRef<{ previousStartedAt?: string } | undefined>(undefined);
  const snapshot = load.key === loadKey ? load.data?.snapshot : null;
  const currentPreview = preview.base === base ? preview : null;
  const currentActivation = activation.base === base ? activation : null;
  const busy = !!currentPreview?.busy;
  const publishing = !!currentActivation?.busy;
  const candidate = currentPreview?.result && currentPreview.result.knowledge.publication_id !== snapshot?.publication_id
    ? currentPreview.result : null;
  const publishedAt = Date.parse(snapshot?.generated_at ?? '');
  const candidateAt = Date.parse(candidate?.brief.generated_at ?? '');
  const staleCandidate = !!candidate && Number.isFinite(publishedAt) && Number.isFinite(candidateAt) && publishedAt > candidateAt;
  const PreviewContainer = staleCandidate ? 'details' : 'section';
  const requestKey = `${base}:${projectLink?.publicationId ?? ''}:${projectLink?.itemId ?? ''}`;
  const expectedPublication = dismissedLink === requestKey ? undefined : projectLink?.publicationId;
  const mismatch = !!snapshot && !!expectedPublication
    && !matchesPublication(snapshot, projectId, expectedPublication);
  const selectedItemId = dismissedLink === requestKey ? undefined : projectLink?.itemId;

  useEffect(() => subscribePublishedKnowledge(base, state => {
    setLoad({ base, key: loadKey, ...state });
  }), [base, loadKey]);
  const lastRefresh = useRef('');
  useEffect(() => {
    if (!visible) return;
    const refreshKey = `${base}:${retry}:${showRecords}`;
    const refresh = !!lastRefresh.current && lastRefresh.current !== refreshKey;
    lastRefresh.current = refreshKey;
    void readPublishedKnowledge(base, { refresh, publicationId: publicationKey || undefined });
  }, [base, retry, visible, publicationKey, showRecords]);

  useEffect(() => () => {
    active.current?.controller.abort();
    active.current = null;
    activating.current?.controller.abort();
    activating.current = null;
    recoveryHandoff.current = undefined;
    lastProgressStartedAt.current = undefined;
  }, [base]);

  useEffect(() => {
    if ((!visible && !busy) || active.current?.base === base || activating.current?.base === base) return;
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
        lastProgressStartedAt.current = status.started_at;
        recoveryHandoff.current = undefined;
        return { ...current, busy: status.state === 'running', loading: false,
          started: status.state === 'running' ? Date.parse(status.started_at || '') || Date.now() : 0,
          cancelled: status.state === 'cancelled',
          error: status.state === 'failed' ? status.error || 'Knowledge generation failed. The saved result is preserved.'
            : status.state === 'idle' && current.busy
              ? 'Generation is no longer running. The saved result is preserved.' : '' };
      });
    }, undefined, undefined, recoveryHandoff.current).catch((error: Error) => {
      if (canApply()) setPreview((previous) => ({ ...previous, busy: false, started: 0, loading: false,
        error: `Could not load the saved result: ${error.message}` }));
    });
    return () => {
      controller.abort();
      if (recovery.current === controller) recovery.current = null;
    };
  }, [base, projectId, retry, visible, loadKey, recoveryAttempt]);

  async function generatePreview() {
    if (externalBusy || active.current || activating.current || busy || tagsBusy) return;
    previewRequestVersion.current++;
    recovery.current?.abort();
    const controller = new AbortController();
    const previousStartedAt = lastProgressStartedAt.current;
    const run = { base, controller };
    active.current = run;
    setActivation({ base, busy: false, error: '', notice: '' });
    setPreview((previous) => ({ base, busy: true, result: previous.base === base ? previous.result : null,
      error: '', started: Date.now(), loading: false }));
    try {
      const raw = await api<unknown>(`${base}/codex/analysis/preview`, {
        method: 'POST', signal: controller.signal,
        body: JSON.stringify({ confirmed: true,
          model: generation.model || null, effort: generation.effort || null }),
      });
      if (!controller.signal.aborted && active.current === run) {
        const result = parseKnowledgePreview(raw, projectId);
        setPreview({ base, busy: false, result, error: '', started: 0 });
      }
    } catch (error) {
      if (!controller.signal.aborted && active.current === run) {
        const cancelled = isGenerationCancelled(error);
        setPreview((previous) => ({ base, busy: !cancelled,
          result: previous.base === base ? previous.result : null,
          error: cancelled ? '' : (error as Error).message,
          started: cancelled ? 0 : previous.started, cancelled }));
        if (!cancelled) {
          recoveryHandoff.current = { previousStartedAt };
          setRecoveryAttempt(previous => previous + 1);
        }
      }
    } finally {
      if (active.current === run) active.current = null;
    }
  }

  async function useResult() {
    if (!candidate || staleCandidate || externalBusy || active.current || activating.current || busy || tagsBusy) return;
    previewRequestVersion.current++;
    recovery.current?.abort();
    const controller = new AbortController();
    const run = { base, controller };
    activating.current = run;
    setActivation({ base, busy: true, error: '', notice: '' });
    try {
      const result = await activateKnowledgeResult(base, projectId, candidate.knowledge.publication_id!, controller.signal);
      if (!controller.signal.aborted && activating.current === run) {
        updatePublishedKnowledge(base, () => ({
          snapshot: result.knowledge, paired_with_current_brief: false,
          current_evidence_availability: {}, coverage: result.knowledge.coverage || {},
        }));
        setActivation({ base, busy: false, error: '', notice: 'Knowledge Summary and records saved. Project Overview is unchanged.' });
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
  const blocked = !!(externalBusy || tagsBusy || busy || publishing || currentPreview?.loading || isLoading);
  useEffect(() => { onBusyChange?.({ busy: blocked, running: busy }); }, [blocked, busy, onBusyChange]);
  useEffect(() => {
    setPreviewView('summary');
  }, [showRecords, candidate?.knowledge.publication_id]);
  useEffect(() => {
    if (lastRequested.current === refreshRequest) return;
    lastRequested.current = refreshRequest;
    void generatePreview();
  }, [refreshRequest]);
  const feedback = <>
    <ErrorBox error={error} />
    <ErrorBox error={currentActivation?.error || ''} />
    <ErrorBox error={currentPreview?.error || ''} />
    {publishing && <Feedback>Saving Knowledge Summary and records…</Feedback>}
    <Feedback tone="success">{currentActivation?.notice}</Feedback>
    {currentPreview?.cancelled && <Feedback>{cancelledGenerationMessage}</Feedback>}
    {busy && visible && <GenerationStatus key={`${base}:${currentPreview?.started}`} base={base}
      kind="knowledge" label="Knowledge Summary" />}
  </>;
  const hasFeedback = !!(error || currentActivation?.error || currentPreview?.error || publishing
    || currentActivation?.notice || currentPreview?.cancelled || busy);
  const absentItem = snapshot && selectedItemId && !snapshot.items.some((item) => item.id === selectedItemId);
  return <section className="knowledge-page">
    {showRecords && snapshot && !mismatch && <PublishedKnowledgeSummary snapshot={snapshot} renderBrief={renderBrief} />}
    {!showRecords && hasFeedback && <section className="card" aria-label="Project analysis status">{feedback}</section>}
    <section className="card" hidden={!showRecords} aria-busy={isLoading || publishing}>
      <div className="section-title">
        <div><h2>Project Knowledge</h2><small>Project #{projectId}</small></div>
        <button disabled={blocked} onClick={generatePreview}>
          {busy ? 'Generating…' : 'Refresh Knowledge Summary'}
        </button>
      </div>
      <p>Explore project choices, reasons and their original evidence.</p>
      <small className="muted">Generation sends selected project evidence to OpenAI through Codex.</small>
      {showRecords && feedback}
      {(isLoading || currentPreview?.loading) && !snapshot && !candidate && <Feedback>Loading saved knowledge…</Feedback>}
      {!isLoading && !currentPreview?.loading && !error && !currentPreview?.error && !snapshot && !candidate
        && <Empty>No saved knowledge yet. Generate a result to get started.</Empty>}
      {!isLoading && !snapshot && expectedPublication
        && <Feedback>The linked knowledge version is not currently available.</Feedback>}
      {snapshot && <>
        <small>Current knowledge{snapshot.generated_at && ` · ${new Date(snapshot.generated_at).toLocaleString()}`}</small>
        <TagGeneration key={`${base}:tags:${snapshot.publication_id}`} base={base} visible={visible} target="knowledge"
          publicationId={snapshot.publication_id} itemIds={snapshot.items.map(item => item.id)}
          generation={generation} hasTags={!!snapshot.topic_groups?.length} disabled={externalBusy || busy || publishing || !snapshot.publication_id}
          onBusyChange={setPublishedTagsBusy} onRecovered={() => setRetry(value => value + 1)}
          onResult={result => updatePublishedKnowledge(base, current => current && current.snapshot && current.snapshot.publication_id === result.publication_id
            ? { ...current, snapshot: { ...current.snapshot,
              topic_groups: result.topic_groups, topic_grouping: result.topic_grouping } } : current)} />
        {mismatch ? <Feedback>
          <p>This link references another Knowledge publication. Its selected record is not being substituted with the current version.</p>
          <button className="secondary" onClick={() => setDismissedLink(requestKey)}>Explore current knowledge</button>
        </Feedback> : <>
          {!!absentItem && <Feedback>The requested item is not in this version. Other current records are shown below.</Feedback>}
          <KnowledgeItems key={`${base}:${snapshot.publication_id ?? snapshot.generated_at ?? 'published'}`}
            snapshot={snapshot} availability={load.data?.current_evidence_availability}
            selectedItemId={absentItem ? undefined : selectedItemId} />
          <CoverageDetails savedAt={snapshot.generated_at} sourceCount={snapshot.sources.length}
            coverage={snapshot.coverage} diagnostics={snapshot.diagnostics}
            note={!snapshot.diagnostics && Object.values(load.data?.coverage ?? {}).some(values => values.length > 0)
              ? 'Processing counts and timings were not recorded for this saved result.' : undefined}>
            {!!Object.values(load.data?.coverage ?? {}).some(values => values.length > 0) && <details>
              <summary className="source-disclosure">Current source availability</summary>
              <p><small>This check describes sources now, separately from the saved generation.</small></p>
              {Object.entries(load.data?.coverage ?? {}).filter(([, values]) => values.length > 0).map(([name, values]) => <div key={name}>
                <strong>{name.replaceAll('_', ' ')}{name === 'limitations' ? '' : ` (${values.length})`}</strong>
                <ul>{values.map((value, index) => <li key={index}>{value}</li>)}</ul>
              </div>)}
            </details>}
          </CoverageDetails>
          {!candidate && currentPreview?.result && <>
            <button className="secondary" onClick={downloadPreview}>Download JSON</button>
          </>}
        </>}
      </>}
    </section>
    {candidate && <PreviewContainer className="card knowledge-preview" aria-label={staleCandidate ? "Older unpublished Knowledge Summary" : "Unpublished Knowledge Summary"}>
      {staleCandidate && <summary className="source-disclosure">Older unpublished Knowledge Summary</summary>}
      <div className="section-title">
        <div><h3>Knowledge Summary</h3><small>Unpublished preview</small>
          <small>{new Date(candidate.brief.generated_at).toLocaleString()}</small></div>
        <button disabled={staleCandidate || externalBusy || tagsBusy || busy || publishing || isLoading || currentPreview?.loading} onClick={useResult}>
          {publishing ? 'Saving…' : 'Save Knowledge Summary'}
        </button>
      </div>
      {staleCandidate ? <p>A newer Knowledge Summary is already saved. Refresh Knowledge Summary to prepare a current replacement.</p>
        : <p>Saving updates the Knowledge Summary and records. Project Overview stays unchanged.</p>}
      <button className="secondary" onClick={downloadPreview}>Download JSON</button>
      <div className="tabs scheme-tabs" role="tablist" aria-label="Knowledge Summary preview">
        <button type="button" role="tab" aria-selected={previewView === 'summary'} className={previewView === 'summary' ? 'active' : ''}
          onClick={() => setPreviewView('summary')}>Knowledge Summary</button>
        <button type="button" role="tab" aria-selected={previewView === 'knowledge'} className={previewView === 'knowledge' ? 'active' : ''}
          onClick={() => setPreviewView('knowledge')}>Knowledge records</button>
      </div>
      <div role="tabpanel" aria-label="Knowledge Summary preview" hidden={previewView !== 'summary'}>
        {renderBrief({ ...candidate.brief, diagnostics: candidate.brief.diagnostics ?? candidate.diagnostics }, 'preview')}
      </div>
      <div role="tabpanel" aria-label="Knowledge records" hidden={previewView !== 'knowledge'}>
      <TagGeneration key={`${base}:preview-tags:${candidate.knowledge.publication_id}`} base={base} visible={visible && previewView === 'knowledge' && !staleCandidate} target="knowledge_preview"
        publicationId={candidate.knowledge.publication_id} itemIds={candidate.knowledge.items.map(item => item.id)}
        generation={generation} hasTags={!!candidate.knowledge.topic_groups?.length} disabled={staleCandidate || externalBusy || busy || publishing || !candidate.knowledge.publication_id}
        onBusyChange={setPreviewTagsBusy} onRecovered={() => setRetry(value => value + 1)}
        onResult={result => setPreview(previous => previous.base === base && previous.result && previous.result.knowledge.publication_id === result.publication_id
          ? { ...previous, result: { ...previous.result, knowledge: { ...previous.result.knowledge,
            topic_groups: result.topic_groups, topic_grouping: result.topic_grouping } } } : previous)} />
      <KnowledgeItems key={`${base}:preview:${candidate.knowledge.publication_id}`} snapshot={candidate.knowledge} />
      <CoverageDetails savedAt={candidate.knowledge.generated_at} sourceCount={candidate.knowledge.sources.length}
        coverage={candidate.knowledge.coverage} diagnostics={candidate.knowledge.diagnostics ?? candidate.diagnostics} />
      </div>
    </PreviewContainer>}
  </section>;
}
