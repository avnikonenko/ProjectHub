import { useGenerationRequest, type GenerationRequestProps } from './generationBatch';
import { Feedback } from './Feedback';
import { BriefContent, BriefCoverage, briefSections, validationCopy, type Brief } from './BriefContent';
import { ImportantKnowledge } from './ImportantKnowledge';
import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { GenerationStatus, SavedGeneration, isGenerationCancelled, cancelledGenerationMessage } from './GenerationStatus';
import { AIResultHeader } from './AIResultHeader';
import { RevisionHistory } from './RevisionHistory';
import { BriefSources, citedSources } from './BriefReferences';
import { Empty, ErrorBox } from './ui';
import { knowledgeHref } from './knowledge';
import { generationBody, generationDestination, type Generation } from './generationProvider';


type GenerationProps = { base: string; evidenceVersion?: string };
type Request = (path: string, options: RequestInit) => Promise<unknown>;

/** Generate only the general Project Overview, independently of Knowledge. */
export async function generateProjectOverview(base: string, generation: Generation,
  signal: AbortSignal, request: Request = api, actionId?: string): Promise<Brief> {
  if (!/^\/projects\/[1-9]\d*$/.test(base)) throw new Error('The project overview does not identify a project.');
  signal.throwIfAborted();
  const result = await request(`${base}/codex/brief`, { method: 'POST', signal,
    body: JSON.stringify({ confirmed: true, ...generationBody(generation, actionId) }) });
  if (!result || typeof result !== 'object' || !('generated_at' in result)
      || typeof result.generated_at !== 'string' || !result.generated_at.trim()
      || !('sections' in result) || !result.sections || typeof result.sections !== 'object'
      || Array.isArray(result.sections) || !('overview' in result.sections) || !Array.isArray(result.sections.overview)
      || !Object.values(result.sections).every(entries => Array.isArray(entries) && entries.every(entry =>
        entry && typeof entry.text === 'string' && Array.isArray(entry.source_ids)
        && entry.source_ids.every((id: unknown) => typeof id === 'string')))
      || !('sources' in result) || !Array.isArray(result.sources)
      || !result.sources.every(source => source && typeof source.id === 'string'
        && typeof source.label === 'string' && typeof source.text === 'string')
      || !('coverage' in result) || !Array.isArray(result.coverage)
      || !result.coverage.every(value => typeof value === 'string'))
    throw new Error('The generated Project Overview is incomplete. Reload the saved result.');
  return result as Brief;
}
type EvidenceFreshness = {
  analysis_generated_at: string | null;
  stale: boolean | null;
  git_error: string | null;
  git_checked_at: string | null;
  history_indexed_at: string | null;
  validation: string;
  git?: { change_detection_limited?: boolean } | null;
};

export function ProjectBrief({ base, evidenceVersion, visible, generation, onGenerating, onRecoveryNeeded, recoveryVersion, recoveryBusy, externalBusy = false, generationRequest = 0, generationActionId, onGenerationRequestHandled, onGenerationRequestSettled, onResultActivated }: GenerationProps & GenerationRequestProps & {
  visible: boolean; generation: Generation; onGenerating: (active: boolean) => void;
  onRecoveryNeeded?: () => void;
  onResultActivated?: () => void;
  recoveryVersion: number; recoveryBusy: boolean; externalBusy?: boolean;
}) {
  const [saved, setSaved] = useState<{ base: string; brief: Brief | null }>({ base, brief: null });
  const brief = saved.base === base ? saved.brief : null;
  const setBrief = (value: Brief | null) => setSaved({ base, brief: value });
  const [latestVersion, setLatestVersion] = useState<string | null>(null);
  const [revisionId, setRevisionId] = useState<number | null>(null);
  const [revisionLoading, setRevisionLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [evidence, setEvidence] = useState<EvidenceFreshness | null>(null);
  const [evidenceRefresh, setEvidenceRefresh] = useState(0);
  const [evidenceError, setEvidenceError] = useState('');
  const [generating, setGenerating] = useState(false);
  const [notice, setNotice] = useState('');
  const activeGeneration = useRef<{ base: string; controller: AbortController } | null>(null);
  const notifyGenerating = useRef(onGenerating);
  notifyGenerating.current = onGenerating;
  const request = useRef(0);
  const selectedRevision = useRef<number | null>(null);
  const selectingRevision = useRef(false);
  useEffect(() => {
    setSaved({ base, brief: null });
    setLatestVersion(null); setRevisionId(null); setRevisionLoading(false);
    setLoading(true); setError(''); setNotice(''); setGenerating(false);
    selectedRevision.current = null; selectingRevision.current = false;
    return () => {
      request.current++;
      if (activeGeneration.current?.base === base) {
        activeGeneration.current.controller.abort();
        activeGeneration.current = null;
        notifyGenerating.current(false);
      }
    };
  }, [base]);
  useEffect(() => {
    if (loading || !visible) return;
    const abort = new AbortController();
    setEvidence(null);
    setEvidenceError('');
    api<EvidenceFreshness>(`${base}/codex/evidence`, { signal: abort.signal })
      .then((result) => {
        if (!abort.signal.aborted) setEvidence(result);
      })
      .catch((e) => {
        if (!abort.signal.aborted) setEvidenceError(`Could not check Project Overview freshness: ${e.message}`);
      });
    return () => abort.abort();
  }, [base, latestVersion, loading, evidenceVersion, visible, evidenceRefresh]);
  useEffect(() => {
    if ((!visible && !generationRequest) || selectingRevision.current || activeGeneration.current) return;
    const abort = new AbortController();
    const id = ++request.current;
    api<Brief | null>(`${base}/codex/brief`, { signal: abort.signal })
      .then((result) => {
        if (!abort.signal.aborted && request.current === id) {
          if (selectedRevision.current === null) setBrief(result);
          setError('');
          setLatestVersion(result?.generated_at ?? null);
        }
      })
      .catch((e) => {
        if (!abort.signal.aborted && request.current === id) setError(e.message);
      })
      .finally(() => {
        if (!abort.signal.aborted && request.current === id) setLoading(false);
      });
    return () => { abort.abort(); request.current += 1; };
  }, [base, recoveryVersion, visible, generationRequest]);

  async function generate(actionId?: string) {
    if (activeGeneration.current || loading || revisionLoading || recoveryBusy || externalBusy) return;
    const run = { base, controller: new AbortController() };
    activeGeneration.current = run;
    request.current++;
    setGenerating(true); setError(''); setNotice('');
    onGenerating(true);
    try {
      const result = await generateProjectOverview(base, generation, run.controller.signal, api, actionId);
      if (activeGeneration.current === run && !run.controller.signal.aborted) {
        selectedRevision.current = null; setRevisionId(null);
        setBrief(result); setLatestVersion(result.generated_at);
      }
    } catch (error) {
      if (activeGeneration.current === run && !run.controller.signal.aborted) {
        if (isGenerationCancelled(error)) setNotice(cancelledGenerationMessage);
        else {
          setError((error as Error).message);
          onRecoveryNeeded?.();
        }
      }
    } finally {
      if (activeGeneration.current === run) {
        activeGeneration.current = null;
        setGenerating(false); onGenerating(false);
      }
    }
  }
  useGenerationRequest(base, generationRequest, generating || recoveryBusy || revisionLoading || externalBusy ? 'skip' : loading ? 'wait' : 'ready', generate,
    onGenerationRequestHandled, generationActionId, onGenerationRequestSettled);
  const visibleSections = briefSections(brief);
  const sources = citedSources(brief?.sources ?? [], visibleSections.flatMap(({ entries }) =>
    entries.flatMap((entry) => entry.source_ids),
  ));
  return (
    <>
      <section className="card project-brief" aria-busy={loading || generating || recoveryBusy || revisionLoading}>
        <AIResultHeader title="Project Overview" savedAt={brief?.generated_at} previous={revisionId !== null}
          freshness={brief && revisionId === null ? evidence?.stale === true ? 'Project changes detected. Regenerate Overview to include them.'
            : evidence?.stale === false ? 'No changes detected in checked evidence' : 'Freshness not checked' : undefined} />
        <p>What the project does, how it is built, and where work stands.</p>
        {(generating || recoveryBusy) && <GenerationStatus base={base} kind="brief" label="Project Overview" />}
        <ErrorBox error={error} />
        <ErrorBox error={evidenceError} />
        <Feedback>{notice}</Feedback>
        {revisionLoading && <Feedback>Loading saved version…</Feedback>}
        {loading && <Feedback>Loading saved Project Overview…</Feedback>}
        {!loading && !brief && <Empty>No saved Project Overview yet. Select Overview on the Generate tab to create one.</Empty>}
        {brief && (
          <>
            <BriefContent brief={brief} prefix={base.split('/')[2] + '-brief'} />
            <a href={knowledgeHref({ projectId: Number(base.split('/')[2]) })}>Search decisions and details in Knowledge</a>
          </>
        )}
      </section>
      <div className="card result-support">
        {brief && <BriefCoverage brief={brief} sourceCount={sources.length} />}
        <details className="brief-details" aria-label="Project Overview details">
          <summary>{brief ? `Summary details & sources (${sources.length})` : 'Generation details'}</summary>
          {brief?.generation && <SavedGeneration generation={brief.generation} />}
          <p className="brief-note">
            Generating sends relevant saved conversations, notes, TODOs, bounded repository code excerpts and Git metadata,
            historical decisions, architecture, and the previous summary to {generationDestination(generation.provider)}.
          </p>
          <RevisionHistory<Brief> base={base} kind="brief" version={latestVersion}
            onActivated={() => {
              setEvidence(null);
              setEvidenceRefresh(value => value + 1);
              onResultActivated?.();
            }}
            disabled={loading || generating || recoveryBusy || externalBusy} selectedId={revisionId} onLoadingChange={active => {
              selectingRevision.current = active;
              if (active) { request.current++; setLoading(false); }
              setRevisionLoading(active);
            }}
            onSelect={(snapshot, id) => {
              request.current++;
              selectedRevision.current = id;
              setBrief(snapshot);
              setRevisionId(id);
              if (id === null) setLatestVersion(snapshot?.generated_at ?? null);
            }} />
          {brief && revisionId === null && evidence && (
            <details>
              <summary>Evidence freshness</summary>
              <p>Git inspected: {evidence.git_checked_at ? new Date(evidence.git_checked_at).toLocaleString() : 'No repository inspection available'}</p>
              <p>History indexed: {evidence.history_indexed_at ? new Date(evidence.history_indexed_at).toLocaleString() : 'Not indexed'}</p>
              <p>{validationCopy(evidence.validation)}</p>
              {evidence.git?.change_detection_limited && <p>Git changes could only be checked partially.</p>}
              {evidence.git_error && <p>Git evidence unavailable: {evidence.git_error}</p>}
            </details>
          )}
          {brief && (
            <details className="brief-sources" aria-label="Project Overview sources">
              <summary>Sources ({sources.length})</summary>
              <BriefSources sources={sources.map((source) => source.id === 'evidence:validation'
                ? { ...source, text: validationCopy(source.text) } : source)} prefix={base.split('/')[2] + '-brief'} />
            </details>
          )}
        </details>
      </div>
      <ImportantKnowledge base={base} publicationId={brief?.project_state?.publication_id} visible={visible} />
    </>
  );
}

