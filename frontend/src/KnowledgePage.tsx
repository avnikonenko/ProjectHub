import { useGenerationRequest, type GenerationRequestProps, type KnowledgeGenerationOutcome, type SchemeFocus } from './generationBatch';
import { TagGeneration } from './TagGeneration';
import { Feedback } from './Feedback';
import { CoverageDetails } from './CoverageDetails';
import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { KnowledgeItems } from './KnowledgeItems';
import {
  matchesPublication, parseKnowledgePreview, parseKnowledgeResponse,
  type KnowledgeCoverage, type KnowledgeLink, type KnowledgeResponse, type KnowledgeSnapshot,
} from './knowledge';
import { Empty, ErrorBox } from './ui';
import { GenerationStatus, isGenerationCancelled, cancelledGenerationMessage } from './GenerationStatus';
import { BriefContent, BriefPreview, BriefSupport } from './BriefContent';
import { generateAndSaveKnowledge, knowledgePreviewExport, recoverPublishedKnowledge, type KnowledgePreview } from './knowledgePreviewRecovery';
import { readPublishedKnowledge, subscribePublishedKnowledge, updatePublishedKnowledge } from './publishedKnowledge';
import { RevisionHistory } from './RevisionHistory';
import type { Generation } from './generationProvider';

type LoadState = { base: string; key: string; loading: boolean; data: KnowledgeResponse | null; error: string };
type PreviewState = { base: string; busy: boolean; error: string; started: number; loading?: boolean; cancelled?: boolean };
type LegacyState = { base: string; result: KnowledgePreview | null; downloadError?: string };
type NoticeState = { base: string; error: string; notice: string };
type Props = {
  base: string;
  generation: Generation;
  link?: KnowledgeLink | null;
  visible?: boolean;
  onPublished?: () => void;
  showRecords?: boolean;
  externalBusy?: boolean;
  refreshRequest?: number;
  generationActionId?: string;
  onGenerationRequestHandled?: GenerationRequestProps['onGenerationRequestHandled'];
  onGenerationRequestSettled?: GenerationRequestProps['onGenerationRequestSettled'];
  onGenerationOutcome?: (outcome: KnowledgeGenerationOutcome) => void;
  generateSchemeAfter?: boolean;
  schemeFocus?: SchemeFocus;
  onBusyChange?: (state: { busy: boolean; running: boolean }) => void;
};

export function PublishedKnowledgeSummary({ snapshot, historical = false }: { snapshot: KnowledgeSnapshot; historical?: boolean }) {
  if (!snapshot.summary && !snapshot.summary_error) return null;
  const summary = snapshot.summary && matchesPublication(snapshot, snapshot.project_id,
    snapshot.summary.project_state?.publication_id) ? snapshot.summary : null;
  const summaryError = snapshot.summary_error || (snapshot.summary && !summary
    ? 'The saved Knowledge Summary does not match this Knowledge version. Records remain available.' : '');
  const prefix = `${snapshot.project_id}-knowledge-published-${snapshot.publication_id ?? 'legacy'}`;
  return <>
    <section className="card knowledge-summary" aria-label="Saved Knowledge Summary">
      <div className="section-title"><div><h2>Knowledge Summary</h2>
        <small>Saved separately from Project Overview</small></div></div>
      <p>Project choices, their reasons, trade-offs, and open questions.</p>
      <ErrorBox error={summaryError} />
      {summary && <BriefContent brief={summary} prefix={prefix} perspective="knowledge" />}
    </section>
    {summary && <BriefSupport brief={summary} prefix={prefix} perspective="knowledge" sourceLabel="Knowledge Summary sources"
      allowSourceNavigation={!historical} />}
  </>;
}

function KnowledgeRecordCoverage({ snapshot, diagnostics = snapshot.diagnostics, currentCoverage }: {
  snapshot: KnowledgeSnapshot;
  diagnostics?: KnowledgeSnapshot['diagnostics'];
  currentCoverage?: KnowledgeCoverage;
}) {
  const currentGroups = Object.entries(currentCoverage ?? {}).filter(([, values]) => values.length > 0);
  return <CoverageDetails savedAt={snapshot.generated_at} sourceCount={snapshot.sources.length}
    coverage={snapshot.coverage} diagnostics={diagnostics}>
    <p>Coverage describes the sources considered for these records. Topics can be discussed without yielding a record.</p>
    {snapshot.extraction_version && <p>Extraction version <code>{snapshot.extraction_version}</code></p>}
    {!!currentGroups.length && <details>
      <summary>Current source availability</summary>
      <p>This check describes source availability now.</p>
      {currentGroups.map(([name, values]) => <div key={name}>
        <strong>{name.replaceAll('_', ' ')}{name === 'limitations' ? '' : ` (${values.length})`}</strong>
        <ul>{values.map((value, index) => <li key={index}>{value}</li>)}</ul>
      </div>)}
    </details>}
  </CoverageDetails>;
}

/** Earlier results are read-only references; generation saves the current result automatically. */
export function EarlierKnowledgeResult({ result, onDownload }: {
  result: KnowledgePreview;
  onDownload: () => void;
}) {
  const [view, setView] = useState<'summary' | 'knowledge'>('summary');
  return <details className="card knowledge-preview" aria-label="Earlier Knowledge Summary">
    <summary className="source-disclosure">Earlier Knowledge Summary</summary>
    <div className="section-title"><div><h3>Knowledge Summary</h3>
      <small>Saved result · {new Date(result.brief.generated_at).toLocaleString()}</small></div></div>
    <p>Earlier version kept for reference.</p>
    <button className="secondary" onClick={onDownload}>Download JSON</button>
    <div className="tabs scheme-tabs" role="tablist" aria-label="Earlier Knowledge version">
      <button type="button" role="tab" aria-selected={view === 'summary'} className={view === 'summary' ? 'active' : ''}
        onClick={() => setView('summary')}>Summary</button>
      <button type="button" role="tab" aria-selected={view === 'knowledge'} className={view === 'knowledge' ? 'active' : ''}
        onClick={() => setView('knowledge')}>Knowledge records</button>
    </div>
    <div role="tabpanel" aria-label="Earlier Knowledge Summary" hidden={view !== 'summary'}>
      <BriefPreview brief={{ ...result.brief, diagnostics: result.brief.diagnostics ?? result.diagnostics }}
        prefix={`${result.knowledge.project_id}-knowledge-preview-${result.brief.project_state?.publication_id ?? 'legacy'}`}
        perspective="knowledge" sourceLabel="Earlier version sources" allowSourceNavigation={false} />
    </div>
    <div role="tabpanel" aria-label="Knowledge records" hidden={view !== 'knowledge'}>
      <KnowledgeItems snapshot={result.knowledge} historical />
      <KnowledgeRecordCoverage snapshot={result.knowledge} diagnostics={result.knowledge.diagnostics ?? result.diagnostics} />
    </div>
  </details>;
}

export function KnowledgePage({ base, generation, link, visible = true, onPublished, showRecords = true, externalBusy = false, refreshRequest = 0, generationActionId, onBusyChange, onGenerationRequestHandled, onGenerationRequestSettled, onGenerationOutcome, generateSchemeAfter = false, schemeFocus }: Props) {
  const projectId = Number(base.split('/')[2]);
  const projectLink = link?.projectId === projectId ? link : null;
  const publicationKey = projectLink?.publicationId ?? '';
  const loadKey = JSON.stringify([base, publicationKey]);
  const [publishedTagsBusy, setPublishedTagsBusy] = useState(false);
  const tagsBusy = publishedTagsBusy;
  const [retry, setRetry] = useState(0);
  const [recoveryAttempt, setRecoveryAttempt] = useState(0);
  const [load, setLoad] = useState<LoadState>({ base, key: loadKey, loading: true, data: null, error: '' });
  const [preview, setPreview] = useState<PreviewState>({ base, busy: false, error: '', started: 0 });
  const [legacy, setLegacy] = useState<LegacyState | null>(null);
  const [notice, setNotice] = useState<NoticeState>({ base, error: '', notice: '' });
  const [dismissedLink, setDismissedLink] = useState('');
  const active = useRef<{ base: string; controller: AbortController } | null>(null);
  const recovery = useRef<AbortController | null>(null);
  const previewRequestVersion = useRef(0);
  const lastProgressStartedAt = useRef<string | undefined>(undefined);
  const recoveryHandoff = useRef<{ previousStartedAt?: string; baselinePublicationId?: string | null } | undefined>(undefined);
  const pendingOutcome = useRef<{ base: string; request: number; baselinePublicationId: string | null; schemeRequested: boolean } | null>(null);
  const outcomeCallback = useRef(onGenerationOutcome); outcomeCallback.current = onGenerationOutcome;
  const [revisionBusy, setRevisionBusy] = useState(false);
  const [historical, setHistorical] = useState<{ route: string; id: number; snapshot: KnowledgeSnapshot } | null>(null);
  const revisionRoute = useRef('');
  function reportOutcome(state: KnowledgeGenerationOutcome['state'], publicationId?: string, schemeRequested?: boolean) {
    const pending = pendingOutcome.current;
    if (!pending || pending.base !== base) return;
    pendingOutcome.current = null;
    outcomeCallback.current?.({ request: pending.request, state, publicationId,
      schemeRequested: state === 'saved' ? schemeRequested ?? pending.schemeRequested : false });
  }
  const snapshot = load.key === loadKey ? load.data?.snapshot : null;
  const currentPreview = preview.base === base ? preview : null;
  const currentLegacy = legacy?.base === base ? legacy : null;
  const currentNotice = notice.base === base ? notice : null;
  const busy = !!currentPreview?.busy;
  const candidate = currentLegacy?.result && currentLegacy.result.knowledge.publication_id !== snapshot?.publication_id
    ? currentLegacy.result : null;
  const requestKey = JSON.stringify([base, publicationKey, projectLink?.itemId]);
  const earlier = historical?.route === requestKey ? historical : null;
  const expectedPublication = dismissedLink === requestKey ? undefined : projectLink?.publicationId;
  const mismatch = !!snapshot && !!expectedPublication
    && !matchesPublication(snapshot, projectId, expectedPublication);
  const selectedItemId = dismissedLink === requestKey ? undefined : projectLink?.itemId;

  function restartPendingRecovery() {
    if (!recovery.current) return;
    recovery.current.abort();
    setRecoveryAttempt(value => value + 1);
  }

  useEffect(() => {
    setHistorical(null);
    setDismissedLink('');
  }, [requestKey]);
  useEffect(() => subscribePublishedKnowledge(base, state => {
    setLoad({ base, key: loadKey, ...state });
  }), [base, loadKey]);
  const lastRefresh = useRef('');
  useEffect(() => {
    if (!visible && !refreshRequest) return;
    const refreshKey = `${requestKey}:${retry}:${showRecords}`;
    const refresh = !!lastRefresh.current && lastRefresh.current !== refreshKey;
    lastRefresh.current = refreshKey;
    void readPublishedKnowledge(base, { refresh, publicationId: publicationKey || undefined });
  }, [base, retry, visible, publicationKey, requestKey, showRecords, refreshRequest]);

  useEffect(() => () => {
    active.current?.controller.abort();
    active.current = null;
    recoveryHandoff.current = undefined;
    lastProgressStartedAt.current = undefined;
    pendingOutcome.current = null;
  }, [base]);

  useEffect(() => {
    if ((!visible && !refreshRequest && !busy) || active.current?.base === base) return;
    const controller = new AbortController();
    recovery.current = controller;
    const version = ++previewRequestVersion.current;
    const canApply = () => !controller.signal.aborted && !active.current
      && version === previewRequestVersion.current;
    setPreview((previous) => previous.base === base ? { ...previous, loading: true }
      : { base, busy: false, error: '', started: 0, loading: true });
    let recoveredPublication: string | null = null;
    void recoverPublishedKnowledge(base, projectId, controller.signal, (event) => {
      if (!canApply()) return;
      if (event.type === 'saved') {
        recoveredPublication = event.result.snapshot?.publication_id ?? null;
        updatePublishedKnowledge(base, () => event.result);
        return;
      }
      const status = event.progress;
      lastProgressStartedAt.current = status.started_at;
      recoveryHandoff.current = undefined;
      if (status.state === 'running' && !pendingOutcome.current) {
        pendingOutcome.current = { base, request: refreshRequest,
          baselinePublicationId: recoveredPublication, schemeRequested: false };
      }
      const unconfirmedSave = status.state === 'completed' && !!pendingOutcome.current && !event.savedForRun;
      if (status.state !== 'running' && pendingOutcome.current) {
        if (event.savedForRun && event.publicationId) {
          reportOutcome('saved', event.publicationId);
          onPublished?.();
        } else reportOutcome(status.state === 'cancelled' ? 'cancelled' : 'failed');
      }
      setPreview((previous) => {
        const current = previous.base === base ? previous
          : { base, busy: false, error: '', started: 0 };
        return { ...current, busy: status.state === 'running', loading: false,
          started: status.state === 'running' ? Date.parse(status.started_at || '') || Date.now() : 0,
          cancelled: status.state === 'cancelled',
          error: status.state === 'failed' ? status.error || 'Knowledge generation failed. The saved result is preserved.'
            : unconfirmedSave ? 'Generation finished without a confirmed new saved result. The previous Knowledge is preserved.'
            : status.state === 'idle' && current.busy
              ? 'Generation is no longer running. The saved result is preserved.' : '' };
      });
    }, undefined, undefined, recoveryHandoff.current).catch((error: Error) => {
      if (canApply()) {
        reportOutcome('failed');
        setPreview((previous) => ({ ...previous, busy: false, started: 0, loading: false,
          error: `Could not load the saved result: ${error.message}` }));
      }
    }).finally(() => {
      if (recovery.current === controller) recovery.current = null;
    });
    return () => {
      controller.abort();
      if (recovery.current === controller) recovery.current = null;
    };
  }, [base, projectId, retry, visible, loadKey, recoveryAttempt, refreshRequest]);

  async function generateKnowledge(actionId?: string) {
    if (externalBusy || active.current || busy || tagsBusy || revisionBusy) return;
    previewRequestVersion.current++;
    recovery.current?.abort();
    const controller = new AbortController();
    const previousStartedAt = lastProgressStartedAt.current;
    const run = { base, controller };
    pendingOutcome.current = { base, request: refreshRequest,
      baselinePublicationId: snapshot?.publication_id ?? null, schemeRequested: generateSchemeAfter };
    active.current = run;
    setHistorical(null);
    setNotice({ base, error: '', notice: '' });
    setPreview({ base, busy: true, error: '', started: Date.now(), loading: false });
    try {
      const result = await generateAndSaveKnowledge(base, projectId, { ...generation, schemeFocus }, controller.signal, generateSchemeAfter, api, actionId);
      if (!controller.signal.aborted && active.current === run) {
        updatePublishedKnowledge(base, () => ({ snapshot: result.knowledge, paired_with_current_brief: false,
          current_evidence_availability: {}, coverage: {} }));
        setPreview({ base, busy: false, error: '', started: 0 });
        setNotice({ base, error: '', notice: 'Knowledge Summary and records saved. Project Overview is unchanged.' });
        reportOutcome('saved', result.knowledge.publication_id, result.schemeRequested === true);
        onPublished?.();
      }
    } catch (error) {
      if (!controller.signal.aborted && active.current === run) {
        const cancelled = isGenerationCancelled(error);
        setPreview((previous) => ({ base, busy: !cancelled,
          error: cancelled ? '' : (error as Error).message,
          started: cancelled ? 0 : previous.started, cancelled }));
        if (cancelled) reportOutcome('cancelled');
        if (!cancelled) {
          recoveryHandoff.current = { previousStartedAt, baselinePublicationId: pendingOutcome.current?.baselinePublicationId };
          setRecoveryAttempt(previous => previous + 1);
        }
      }
    } finally {
      if (active.current === run) active.current = null;
    }
  }

  function downloadPreview() {
    if (!currentLegacy?.result) return;
    try {
      const exported = knowledgePreviewExport(currentLegacy.result, projectId);
      const url = URL.createObjectURL(new Blob([exported.text], { type: 'application/json;charset=utf-8' }));
      const anchor = document.createElement('a');
      anchor.href = url; anchor.download = exported.filename;
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      setLegacy({ ...currentLegacy, downloadError: (error as Error).message });
    }
  }

  useEffect(() => {
    if (!visible || !showRecords || currentLegacy) return;
    const controller = new AbortController();
    void api<unknown>(`${base}/codex/analysis/preview`, { signal: controller.signal }).then(raw => {
      if (!controller.signal.aborted) {
        const result = raw === null ? null : parseKnowledgePreview(raw, projectId);
        setLegacy({ base, result });
      }
    }).catch(() => {
      // Earlier results are optional. Allow the next visit to retry this lookup.
    });
    return () => controller.abort();
  }, [base, projectId, visible, showRecords, currentLegacy]);

  const isLoading = load.key !== loadKey || load.loading;
  const error = load.key === loadKey ? load.error : '';
  const blocked = !!(externalBusy || tagsBusy || busy || revisionBusy || currentPreview?.loading || isLoading);
  useEffect(() => { onBusyChange?.({ busy: blocked, running: busy }); }, [blocked, busy, onBusyChange]);
  useGenerationRequest(base, refreshRequest, externalBusy || tagsBusy || busy || revisionBusy ? 'skip' : currentPreview?.loading || isLoading ? 'wait' : 'ready', generateKnowledge,
    onGenerationRequestHandled, generationActionId, onGenerationRequestSettled);
  const feedback = <>
    <ErrorBox error={error} />
    <ErrorBox error={currentNotice?.error || ''} />
    <ErrorBox error={currentPreview?.error || ''} />
    <Feedback tone="success">{currentNotice?.notice}</Feedback>
    {currentPreview?.cancelled && <Feedback>{cancelledGenerationMessage}</Feedback>}
    {busy && visible && <GenerationStatus key={`${base}:${currentPreview?.started}`} base={base}
      kind="knowledge" label="Knowledge Summary" />}
  </>;
  const hasFeedback = !!(error || currentNotice?.error || currentPreview?.error
    || currentNotice?.notice || currentPreview?.cancelled || busy);
  const absentItem = snapshot && selectedItemId && !snapshot.items.some((item) => item.id === selectedItemId);
  return <section className="knowledge-page">
    {showRecords && <RevisionHistory<KnowledgeSnapshot> key={base} base={base} kind="knowledge"
      version={snapshot?.generated_at ?? null} selectedId={earlier?.id ?? null}
      disabled={!!(externalBusy || tagsBusy || busy || currentPreview?.loading || isLoading)}
      onLoadingChange={loading => {
        if (loading) revisionRoute.current = requestKey;
        setRevisionBusy(loading);
      }}
      onSelect={(saved, id) => {
        if (revisionRoute.current !== requestKey) return;
        const snapshot = saved && id !== null ? parseKnowledgeResponse({ snapshot: saved }, projectId).snapshot : null;
        setHistorical(snapshot && id !== null ? { route: requestKey, id, snapshot } : null);
      }}
      onActiveRead={result => {
        if (revisionRoute.current !== requestKey) return;
        const response = parseKnowledgeResponse(result, projectId);
        restartPendingRecovery();
        updatePublishedKnowledge(base, () => response);
        setHistorical(null);
        setDismissedLink(requestKey);
      }}
      onActivated={() => {
        if (revisionRoute.current === requestKey) setDismissedLink(requestKey);
        restartPendingRecovery();
        updatePublishedKnowledge(base, () => null);
        void readPublishedKnowledge(base, { refresh: true });
        onPublished?.();
      }} />}
    {showRecords && earlier && <>
      <PublishedKnowledgeSummary snapshot={earlier.snapshot} historical />
      <section className="card">
        <h2>Knowledge records</h2>
        <KnowledgeItems key={earlier.id} snapshot={earlier.snapshot} historical />
      </section>
      <KnowledgeRecordCoverage snapshot={earlier.snapshot} />
    </>}
    {showRecords && !earlier && snapshot && !mismatch && <PublishedKnowledgeSummary snapshot={snapshot} />}
    {!showRecords && hasFeedback && <section className="card" aria-label="Project analysis status">{feedback}</section>}
    <section className="card" hidden={!showRecords || !!earlier} aria-busy={isLoading}>
      <div className="section-title">
        <div><h2>Project Knowledge</h2><small>Project #{projectId}</small></div>
      </div>
      <p>Explore project choices, reasons and their original evidence.</p>
      {showRecords && feedback}
      {(isLoading || currentPreview?.loading) && !snapshot && !candidate && <Feedback>Loading saved knowledge…</Feedback>}
      {!isLoading && !currentPreview?.loading && !error && !currentPreview?.error && !snapshot && !candidate
        && <Empty>No current Knowledge result. Select Knowledge on the Generate tab to create one.</Empty>}
      {!isLoading && !snapshot && expectedPublication
        && <Feedback>The linked knowledge version is not currently available.</Feedback>}
      {snapshot && <>
        <small>Current knowledge{snapshot.generated_at && ` · ${new Date(snapshot.generated_at).toLocaleString()}`}</small>
        <TagGeneration key={`${base}:tags:${snapshot.publication_id}`} base={base} visible={visible} target="knowledge"
          publicationId={snapshot.publication_id} itemIds={snapshot.items.map(item => item.id)}
          generation={generation} hasTags={!!snapshot.topic_groups?.length} disabled={externalBusy || busy || !snapshot.publication_id}
          onBusyChange={setPublishedTagsBusy} onRecovered={() => setRetry(value => value + 1)}
          onResult={result => updatePublishedKnowledge(base, current => current && current.snapshot && current.snapshot.publication_id === result.publication_id
            ? { ...current, snapshot: { ...current.snapshot,
              topic_groups: result.topic_groups, topic_grouping: result.topic_grouping } } : current)} />
        {mismatch ? <Feedback>
          <p>This link references another Knowledge version. Its selected record is not being substituted with the current version.</p>
          <button className="secondary" onClick={() => setDismissedLink(requestKey)}>Explore current knowledge</button>
        </Feedback> : <>
          {!!absentItem && <Feedback>The requested item is not in this version. Other current records are shown below.</Feedback>}
          <KnowledgeItems key={`${base}:${snapshot.publication_id ?? snapshot.generated_at ?? 'published'}`}
            snapshot={snapshot} availability={load.data?.current_evidence_availability}
            selectedItemId={absentItem ? undefined : selectedItemId} />
          {!candidate && currentLegacy?.result && <>
            <button className="secondary" onClick={downloadPreview}>Download JSON</button>
            <ErrorBox error={currentLegacy.downloadError ?? ''} />
          </>}
        </>}
      </>}
    </section>
    {showRecords && !earlier && snapshot && !mismatch && <KnowledgeRecordCoverage snapshot={snapshot} currentCoverage={load.data?.coverage} />}
    {showRecords && !earlier && !isLoading && candidate && <>
      <EarlierKnowledgeResult key={`${base}:${candidate.knowledge.publication_id}`}
        result={candidate} onDownload={downloadPreview} />
      <ErrorBox error={currentLegacy?.downloadError ?? ''} />
    </>}
  </section>;
}
