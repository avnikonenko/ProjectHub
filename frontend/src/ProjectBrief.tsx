import { Feedback } from './Feedback';
import { BriefContent, briefSections, validationCopy, type Brief } from './BriefContent';
import { ImportantKnowledge } from './ImportantKnowledge';
import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { SavedGeneration } from './GenerationStatus';
import { AIResultHeader } from './AIResultHeader';
import { RevisionHistory } from './RevisionHistory';
import { BriefSources, citedSources } from './BriefReferences';
import { Empty, ErrorBox } from './ui';


type GenerationProps = { base: string; evidenceVersion?: string };
type EvidenceFreshness = {
  analysis_generated_at: string | null;
  stale: boolean | null;
  git_error: string | null;
  git_checked_at: string | null;
  history_indexed_at: string | null;
  validation: string;
  git?: { change_detection_limited?: boolean } | null;
};

export function ProjectBrief({ base, evidenceVersion, visible, onRefresh, analysisBusy, analysisRunning, recoveryVersion }: GenerationProps & {
  visible: boolean; onRefresh: () => void; analysisBusy: boolean; analysisRunning: boolean; recoveryVersion: number;
}) {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [latestVersion, setLatestVersion] = useState<string | null>(null);
  const [revisionId, setRevisionId] = useState<number | null>(null);
  const [revisionLoading, setRevisionLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [evidence, setEvidence] = useState<EvidenceFreshness | null>(null);
  const [evidenceError, setEvidenceError] = useState('');
  const request = useRef(0);
  const selectedRevision = useRef<number | null>(null);
  const selectingRevision = useRef(false);
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
    if (!visible || selectingRevision.current) return;
    const id = ++request.current;
    api<Brief | null>(`${base}/codex/brief`)
      .then((result) => {
        if (request.current === id) {
          if (selectedRevision.current === null) setBrief(result);
          setError('');
          setLatestVersion(result?.generated_at ?? null);
        }
      })
      .catch((e) => {
        if (request.current === id) setError(e.message);
      })
      .finally(() => {
        if (request.current === id) setLoading(false);
      });
    return () => { request.current += 1; };
  }, [base, recoveryVersion, visible]);
  const visibleSections = briefSections(brief);
  const sources = citedSources(brief?.sources ?? [], visibleSections.flatMap(({ entries }) =>
    entries.flatMap((entry) => entry.source_ids),
  ));
  return (
    <>
      <section className="card project-brief" aria-busy={loading || analysisBusy}>
        <AIResultHeader title="Project status" savedAt={brief?.generated_at} previous={revisionId !== null}
          freshness={brief && revisionId === null ? evidence?.stale === true ? 'Project changes detected. Refresh to include them.'
            : evidence?.stale === false ? 'No changes detected in checked evidence' : 'Freshness not checked' : undefined}
          action={<button disabled={loading || analysisBusy || revisionLoading} onClick={onRefresh}>
            {analysisRunning ? 'Generating…' : 'Refresh project analysis'}
          </button>} />
        <small className="brief-refresh-note">Refreshing sends project context to OpenAI.</small>
        <ErrorBox error={error} />
        <ErrorBox error={evidenceError} />
        {revisionLoading && <Feedback>Loading saved version…</Feedback>}
        {loading && <Feedback>Loading saved project status…</Feedback>}
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
          disabled={loading || analysisBusy} selectedId={revisionId} onLoadingChange={active => {
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
          <details className="brief-sources" aria-label="Project status sources">
            <summary>Sources ({sources.length})</summary>
            <BriefSources sources={sources.map((source) => source.id === 'evidence:validation'
              ? { ...source, text: validationCopy(source.text) } : source)} prefix={base.split('/')[2] + '-brief'} />
          </details>
        )}
      </details>
    </>
  );
}

