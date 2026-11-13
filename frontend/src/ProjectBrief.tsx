import { BriefContent, briefSections, validationCopy, type Brief } from './BriefContent';
import { ImportantKnowledge } from './ImportantKnowledge';
import { useEffect, useRef, useState } from 'react';
import { api, post } from './api';
import { SavedGeneration, isGenerationCancelled, cancelledGenerationMessage } from './GenerationStatus';
import { AIResultHeader } from './AIResultHeader';
import { RevisionHistory } from './RevisionHistory';
import { BriefSources, citedSources } from './BriefReferences';
import { Empty, ErrorBox } from './ui';


type GenerationProps = { base: string; generation: { model: string; effort: string }; evidenceVersion?: string };
type EvidenceFreshness = {
  analysis_generated_at: string | null;
  stale: boolean | null;
  git_error: string | null;
  git_checked_at: string | null;
  history_indexed_at: string | null;
  validation: string;
  git?: { change_detection_limited?: boolean } | null;
};

function generationBody({ model, effort }: GenerationProps['generation']) {
  return { model: model || null, effort: effort || null };
}


export function ProjectBrief({ base, generation, evidenceVersion, visible, onGenerating, recoveryVersion, recoveryBusy }: GenerationProps & { visible: boolean; onGenerating: (active: boolean) => void; recoveryVersion: number; recoveryBusy: boolean }) {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [latestVersion, setLatestVersion] = useState<string | null>(null);
  const [revisionId, setRevisionId] = useState<number | null>(null);
  const [revisionLoading, setRevisionLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [evidence, setEvidence] = useState<EvidenceFreshness | null>(null);
  const [evidenceError, setEvidenceError] = useState('');
  const request = useRef(0);
  const generatingRef = useRef(false);
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
        if (!abort.signal.aborted) setEvidenceError(`Could not check project status freshness: ${e.message}`);
      });
    return () => abort.abort();
  }, [base, latestVersion, loading, evidenceVersion, visible]);
  useEffect(() => {
    const id = ++request.current;
    api<Brief | null>(`${base}/codex/brief`)
      .then((result) => {
        if (request.current === id) {
          setBrief(result);
          setError('');
          setLatestVersion(result?.generated_at ?? null);
          setRevisionId(null);
        }
      })
      .catch((e) => {
        if (request.current === id) setError(e.message);
      })
      .finally(() => {
        if (request.current === id) setLoading(false);
      });
    return () => { request.current += 1; };
  }, [base, recoveryVersion]);
  async function generate() {
    if (generatingRef.current || loading || revisionLoading || recoveryBusy) return;
    generatingRef.current = true;
    const id = ++request.current;
    setGenerating(true);
    onGenerating(true);
    setError('');
    setNotice('');
    try {
      const result = await post(`${base}/codex/brief`, { confirmed: true, ...generationBody(generation) });
      if (request.current === id) {
        setBrief(result);
        setRevisionId(null);
        setLatestVersion(result.generated_at);
      }
    } catch (e) {
      if (request.current === id) {
        if (isGenerationCancelled(e)) setNotice(cancelledGenerationMessage);
        else setError((e as Error).message);
      }
    } finally {
      generatingRef.current = false;
      if (request.current === id) {
        setGenerating(false);
        onGenerating(false);
      }
    }
  }
  const visibleSections = briefSections(brief);
  const sources = citedSources(brief?.sources ?? [], visibleSections.flatMap(({ entries }) =>
    entries.flatMap((entry) => entry.source_ids),
  ));
  return (
    <>
      <section className="card project-brief" aria-busy={loading || generating || recoveryBusy}>
        <AIResultHeader title="Project status" savedAt={brief?.generated_at} previous={revisionId !== null}
          freshness={brief && revisionId === null ? evidence?.stale === true ? 'Project changes detected. Refresh to include them.'
            : evidence?.stale === false ? 'No changes detected in checked evidence' : 'Freshness not checked' : undefined}
          action={<button disabled={loading || generating || recoveryBusy || revisionLoading} onClick={generate}>
            {generating || recoveryBusy ? 'Generating…' : brief ? 'Refresh project status' : 'Generate project status'}
          </button>} />
        <small className="brief-refresh-note">Refreshing sends project context to OpenAI.</small>
        <ErrorBox error={error} />
        {notice && <p role="status">{notice}</p>}
        {revisionLoading && <p role="status">Loading saved version…</p>}
        {loading && <Empty>Loading saved project status…</Empty>}
        {!loading && !brief && <Empty>No saved project status yet.</Empty>}
        {brief && (
          <>
            <BriefContent brief={brief} prefix={base.split('/')[2] + '-brief'} />
          </>
        )}
      </section>
      <ImportantKnowledge base={base} publicationId={brief?.project_state?.publication_id} visible={visible} />
      <details className="card brief-details" aria-label="Project status details">
        <summary>{brief ? `Summary details & sources (${sources.length})` : 'Generation details'}</summary>
        {brief?.generation && <SavedGeneration generation={brief.generation} />}
        <p className="brief-note">
          Generating sends relevant saved conversations, notes, TODOs, bounded repository code excerpts and Git metadata,
          historical decisions, architecture, and the previous summary to OpenAI.
        </p>
        <RevisionHistory<Brief> base={base} kind="brief" version={latestVersion}
          disabled={loading || generating || recoveryBusy} selectedId={revisionId} onLoadingChange={setRevisionLoading}
          onSelect={(snapshot, id) => {
            setBrief(snapshot);
            setRevisionId(id);
            if (id === null) setLatestVersion(snapshot?.generated_at ?? null);
          }} />
        <ErrorBox error={evidenceError} />
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
          <details className="brief-sources" aria-label="Project status sources">
            <summary>Sources ({sources.length})</summary>
            <BriefSources sources={sources.map((source) => source.id === 'evidence:validation'
              ? { ...source, text: validationCopy(source.text) } : source)} prefix={base.split('/')[2] + '-brief'} />
            <details>
              <summary>What the summary covers</summary>
              <ul>{brief.coverage.map((item, index) => <li key={index}>{validationCopy(item)}</li>)}</ul>
            </details>
          </details>
        )}
      </details>
    </>
  );
}

