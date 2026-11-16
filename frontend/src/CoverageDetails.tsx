import type { ReactNode } from 'react';
import { GenerationDetails } from './GenerationDetails';
import { parseGenerationDiagnostics } from './generationDiagnostics';

type Props = {
  savedAt?: string | null;
  sourceCount?: number;
  coverage?: unknown;
  diagnostics?: unknown;
  children?: ReactNode;
  note?: string;
};

function coverageGroups(raw: unknown): [string, string[]][] {
  if (Array.isArray(raw)) return [['Recorded coverage', raw.filter((value): value is string => typeof value === 'string')]];
  if (!raw || typeof raw !== 'object') return [];
  return Object.entries(raw).flatMap(([name, values]) => Array.isArray(values)
    ? [[name.replaceAll('_', ' '), values.filter((value): value is string => typeof value === 'string')] as [string, string[]]] : []);
}

/** Only metadata belonging to this saved result is supplied by callers. */
export function CoverageDetails({ savedAt, sourceCount, coverage, diagnostics, children, note }: Props) {
  const measured = parseGenerationDiagnostics(diagnostics);
  const hasMeasurements = measured && Object.keys(measured).length > 0;
  const recordedSourceCount = Number.isSafeInteger(sourceCount) && sourceCount! >= 0
    ? sourceCount : measured?.cited_source_count;
  const groups = coverageGroups(coverage).filter(([, values]) => values.length > 0);
  const date = savedAt ? new Date(savedAt) : null;
  return <details className="coverage-details generation-details">
    <summary className="source-disclosure">Coverage &amp; technical details</summary>
    {date && Number.isFinite(date.getTime()) && <p><small>Recorded with the result saved {date.toLocaleString()}.</small></p>}
    {note && <p><small>{note}</small></p>}
    {recordedSourceCount !== undefined && <p>Cited sources in this result: {recordedSourceCount}.</p>}
    {groups.map(([name, values], index) => <section key={`${name}:${index}`}>
      <h4>{name.charAt(0).toUpperCase() + name.slice(1)}</h4>
      <ul>{values.map((value, item) => <li key={item}>{value}</li>)}</ul>
    </section>)}
    {hasMeasurements ? <GenerationDetails diagnostics={measured} embedded />
      : !children && <p>Processing counts and timings were not recorded for this saved result.</p>}
    {children}
  </details>;
}
