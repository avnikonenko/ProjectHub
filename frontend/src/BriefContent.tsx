import { AIEmphasis } from './AIEmphasis';
import { type GenerationProvenance } from './GenerationStatus';
import { BriefSources, briefReferences, citedSources, type BriefSource } from './BriefReferences';
import { Empty } from './ui';

export function validationCopy(text: string) {
  return text === 'No independently recorded test execution or scientific validation result is available. '
    + 'Test files and statements in conversations are not evidence of a passing test run.'
    ? 'No verified test or validation results.'
    : text;
}

type BriefEntry = { text: string; source_ids: string[]; classification?: 'verified' | 'reported' | 'inferred' | 'unresolved' };
const evidenceLabels = { verified: 'Verified observation', reported: 'Reported', inferred: 'Inference', unresolved: 'Unresolved' };
export type Brief = {
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
  project_state?: { publication_id?: string; pipeline?: string };
};


export function briefSections(brief: Brief | null) {
  const normalizePoint = (text: string) => text.trim().toLocaleLowerCase().replace(/\s+/g, ' ');
  const overviewTexts = new Set((brief?.sections.overview ?? []).map((entry) => normalizePoint(entry.text)));
  const currentWork = new Map<string, BriefEntry>();
  for (const entry of [...(brief?.sections.current_status ?? []), ...(brief?.sections.work_in_progress ?? [])]) {
    const text = normalizePoint(entry.text);
    if (overviewTexts.has(text)) continue;
    const existing = currentWork.get(text);
    currentWork.set(text, existing ? {
      ...existing, source_ids: [...new Set([...existing.source_ids, ...entry.source_ids])],
    } : entry);
  }
  const sections: [keyof Brief['sections'], string, boolean][] = [
    ['overview', 'Overview', false],
    ['architecture', 'Reported architecture', false],
    ['current_status', 'Current work', false],
    ['unresolved_problems', 'Unresolved problems', false],
    ['open_questions', 'Open questions', false],
    ['next_steps', 'Recommended next steps', false],
    ['since_last_brief', 'Since your last update', true],
    ['changes', 'Recent changes', true],
    ['implemented', 'Implemented work', true],
    ['validation', 'Validation', true],
  ];
  return sections.flatMap(([key, title, collapsed]) => {
    const entries = key === 'current_status' ? [...currentWork.values()] : brief?.sections[key];
    if (key === 'architecture' && !entries?.length) {
      return [{ key, title, collapsed, entries: [
        { text: 'Architecture not documented in this saved summary.', source_ids: [] },
      ] }];
    }
    return entries?.length ? [{ key, title, collapsed, entries }] : [];
  });
}

export function BriefContent({ brief, prefix }: { brief: Brief; prefix: string }) {
  const selectionNotice = brief.coverage.find(item => item.startsWith('Overview uses '));
  const visibleSections = briefSections(brief);
  const sources = citedSources(brief.sources ?? [], visibleSections.flatMap(({ entries }) => entries.flatMap((entry) => entry.source_ids)));
  const sourceNumbers = new Map(sources.map((source, index) => [source.id, index + 1]));
  return <>
    {selectionNotice && <p className="muted" role="status">{selectionNotice}</p>}
    {visibleSections.map(({ key, title, collapsed, entries }) => {
      const points = (
        <ul className="brief-points">
          {entries.map((entry, index) => (
            <li key={index}>
              {entry.classification && <><small>{evidenceLabels[entry.classification]}</small>{' · '}</>}
              <AIEmphasis text={key === 'validation' ? validationCopy(entry.text) : entry.text} />
              {briefReferences(entry.source_ids, sourceNumbers, prefix, brief.sources)}
            </li>
          ))}
        </ul>
      );
      return collapsed ? (
        <details className="brief-section" key={key}><summary className="brief-topic">{title}</summary>{points}</details>
      ) : (
        <section className="brief-section" key={key}><h3 className="brief-topic">{title}</h3>{points}</section>
      );
    })}
    {!visibleSections.length && <Empty>The available sources did not support a project summary.</Empty>}
  </>;
}

export function BriefPreview({ brief, prefix }: { brief: Brief; prefix: string }) {
  const sources = citedSources(brief.sources, briefSections(brief).flatMap(({ entries }) => entries.flatMap(entry => entry.source_ids)));
  return <>
    <BriefContent brief={brief} prefix={prefix} />
    <details><summary className="source-disclosure">Preview sources ({sources.length})</summary><BriefSources sources={sources} prefix={prefix} /></details>
    {!!brief.coverage.length && <details><summary className="source-disclosure">Preview coverage</summary><ul>{brief.coverage.map((item, index) => <li key={index}>{item}</li>)}</ul></details>}
  </>;
}
