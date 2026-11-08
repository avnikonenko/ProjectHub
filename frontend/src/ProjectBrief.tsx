import { useEffect, useRef, useState } from 'react';
import { api, post } from './api';
import { SavedGeneration, type GenerationProvenance } from './GenerationStatus';
import { RevisionHistory } from './RevisionHistory';
import { BriefSources, briefReferences, citedSources, type BriefSource } from './BriefReferences';
import { Empty, ErrorBox } from './ui';

function validationCopy(text: string) {
  return text === 'No independently recorded test execution or scientific validation result is available. '
    + 'Test files and statements in conversations are not evidence of a passing test run.'
    ? 'No verified test or validation results.'
    : text;
}

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

type BriefEntry = { text: string; source_ids: string[]; classification?: 'verified' | 'reported' | 'inferred' | 'unresolved' };
const evidenceLabels = { verified: 'Verified observation', reported: 'Reported', inferred: 'Inference', unresolved: 'Unresolved' };
type Brief = {
  generated_at: string;
  generation?: GenerationProvenance | null;
  sections: {
    since_last_brief?: BriefEntry[];
    current_status?: BriefEntry[];
    implemented?: BriefEntry[];
    work_in_progress?: BriefEntry[];
    unresolved_problems?: BriefEntry[];
    validation?: BriefEntry[];
    next_steps?: BriefEntry[];
    overview: BriefEntry[];
    changes: BriefEntry[];
    architecture?: BriefEntry[];
    open_questions: BriefEntry[];
    decision_candidates: BriefEntry[];
  };
  sources: BriefSource[];
  coverage: string[];
};

export function ProjectBrief({ base, generation, evidenceVersion, visible, onGenerating }: GenerationProps & { visible: boolean; onGenerating: (active: boolean) => void }) {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [latestVersion, setLatestVersion] = useState<string | null>(null);
  const [revisionId, setRevisionId] = useState<number | null>(null);
  const [revisionLoading, setRevisionLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
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
  }, [base]);
  async function generate() {
    if (generatingRef.current || loading || revisionLoading) return;
    generatingRef.current = true;
    const id = ++request.current;
    setGenerating(true);
    onGenerating(true);
    setError('');
    try {
      const result = await post(`${base}/codex/brief`, { confirmed: true, ...generationBody(generation) });
      if (request.current === id) {
        setBrief(result);
        setRevisionId(null);
        setLatestVersion(result.generated_at);
      }
    } catch (e) {
      if (request.current === id) setError((e as Error).message);
    } finally {
      generatingRef.current = false;
      if (request.current === id) {
        setGenerating(false);
        onGenerating(false);
      }
    }
  }
  const hasCurrentStatus = !!brief?.sections.current_status?.length;
  const sections: [keyof Brief['sections'], string, boolean][] = [
    ['current_status', 'Current status', false],
    ['overview', hasCurrentStatus ? 'Project objective' : 'Overview', hasCurrentStatus],
    ['unresolved_problems', 'Unresolved problems', false],
    ['open_questions', 'Open questions', false],
    ['next_steps', 'Recommended next steps', false],
    ['since_last_brief', 'Since your last update', true],
    ['changes', 'Recent changes', true],
    ['implemented', 'Implemented work', true],
    ['work_in_progress', 'Work in progress', true],
    ['validation', 'Validation', true],
    ['architecture', 'Architecture', true],
  ];
  const visibleSections = sections.flatMap(([key, title, collapsed]) => {
    const entries = brief?.sections[key];
    return entries?.length ? [{ key, title, collapsed, entries }] : [];
  });
  const sources = citedSources(brief?.sources ?? [], visibleSections.flatMap(({ entries }) =>
    entries.flatMap((entry) => entry.source_ids),
  ));
  const sourceNumbers = new Map(sources.map((source, index) => [source.id, index + 1]));
  return (
    <>
      <section className="card project-brief" aria-busy={loading || generating}>
        <div className="section-title">
          <div>
            <h2>Project status</h2>
            {brief && <small>{revisionId === null ? 'AI summary' : 'Previous version'} · {new Date(brief.generated_at).toLocaleString()}</small>}
          </div>
          <button disabled={loading || generating || revisionLoading} onClick={generate}>
            {generating ? 'Generating…' : brief ? 'Refresh project status' : 'Generate project status'}
          </button>
        </div>
        <small className="brief-refresh-note">Refreshing sends project context to OpenAI.</small>
        <ErrorBox error={error} />
        {revisionLoading && <p role="status">Loading saved version…</p>}
        {brief && revisionId === null && evidence?.stale === true && (
          <p role="status">Project changes detected. Refresh to include them.</p>
        )}
        {loading && <Empty>Loading saved project status…</Empty>}
        {!loading && !brief && <Empty>No saved project status yet.</Empty>}
        {brief && (
          <>
            {visibleSections.map(({ key, title, collapsed, entries }) => {
              const points = (
                <ul className="brief-points">
                  {entries.map((entry, index) => (
                    <li key={index}>
                      {entry.classification && <><small>{evidenceLabels[entry.classification]}</small>{' · '}</>}
                      {key === 'validation' ? validationCopy(entry.text) : entry.text}
                      {briefReferences(entry.source_ids, sourceNumbers, base.split('/')[2] + '-brief')}
                    </li>
                  ))}
                </ul>
              );
              return collapsed ? (
                <details className="brief-section" key={key}><summary>{title}</summary>{points}</details>
              ) : (
                <section className="brief-section" key={key}><h3>{title}</h3>{points}</section>
              );
            })}
            {!visibleSections.length && <Empty>The available sources did not support a project summary.</Empty>}
          </>
        )}
        <details className="brief-meta">
          <summary>Summary details</summary>
          {brief?.generation && <SavedGeneration generation={brief.generation} />}
          <p className="brief-note">
            Generating sends relevant saved conversations, notes, TODOs, Git metadata and file paths,
            historical decisions, architecture, and the previous summary to OpenAI.
          </p>
          <RevisionHistory<Brief> base={base} kind="brief" version={latestVersion}
            disabled={loading || generating} selectedId={revisionId} onLoadingChange={setRevisionLoading}
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
        </details>
      </section>
      {brief && (
        <details className="card brief-sources" aria-label="Project status sources">
          <summary>Sources ({sources.length})</summary>
          <BriefSources sources={sources.map((source) => source.id === 'evidence:validation'
            ? { ...source, text: validationCopy(source.text) } : source)} prefix={base.split('/')[2] + '-brief'} />
          <details>
            <summary>What the summary covers</summary>
            <ul>{brief.coverage.map((item, index) => <li key={index}>{validationCopy(item)}</li>)}</ul>
          </details>
        </details>
      )}
    </>
  );
}

