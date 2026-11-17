import { CoverageDetails } from './CoverageDetails';

export type SchemeFileCounts = {
  repository_paths_total: number | null; excluded_paths: number | null;
  safe_paths: number | null; eligible_files: number | null; policy_omitted_paths: number | null;
  attempted_files: number; read_files: number; prefix_read_files: number; failed_reads: number;
  not_attempted_files: number | null; evidence_files: number; read_not_in_evidence: number;
  listing_complete: boolean;
};
export type SchemeInspectionPolicy = {
  max_read_bytes: number; max_inspection_seconds: number; max_file_bytes: number;
  max_source_chars: number; max_package_chars: number; max_tree_paths: number;
  max_metadata_bytes: number; max_metadata_seconds: number;
};
export type SchemeDiagnostics = {
  bytes_read?: number; inspection_seconds?: number; cache_hits?: number; package_chars?: number;
};
export type SchemeCoverage = {
  partial: boolean; limitations: string[]; files_inspected: number; conversation_excerpts: number;
  file_counts?: SchemeFileCounts | null; inspection_policy?: SchemeInspectionPolicy | null;
  inspection_stopped?: string | null; omitted_reason_counts?: Record<string, number>;
  file_omissions?: { files: { path: string; reason: string }[]; total: number };
};
type Inspection = { coverage?: SchemeCoverage; diagnostics?: SchemeDiagnostics | null };

// Saved results must retain their own evidence accounting, including when an old
// revision has no counts. A fresh GET inspection describes different evidence.
export function schemeInspectionForDisplay(snapshot: Inspection | null | undefined, current: Inspection | undefined) {
  return snapshot ? { coverage: snapshot.coverage, diagnostics: snapshot.diagnostics, saved: true }
    : { coverage: current?.coverage, diagnostics: current?.diagnostics, saved: false };
}
function nonnegative(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}
function count(value: unknown) {
  return nonnegative(value) && Number.isSafeInteger(value) ? String(value) : 'Not recorded';
}
const omissionLabels: Record<string, string> = {
  not_attempted: 'Inspection did not reach this file',
  time_budget: 'Inspection time limit reached',
  byte_budget: 'Local read budget reached',
  metadata_time_budget: 'File metadata inspection time limit reached',
  metadata_byte_budget: 'File metadata inspection budget reached',
  unsafe_or_unavailable: 'Could not safely open the file',
  unreadable_or_malformed: 'File could not be read or parsed',
  no_usable_excerpt: 'No usable excerpt produced',
  source_output_budget: 'Source excerpt budget reached',
  package_output_budget: 'Final evidence package limit reached',
  knowledge_support_budget: 'Supporting evidence did not fit the input budget',
};
function omissionLabel(reason: string) {
  return Object.hasOwn(omissionLabels, reason) ? omissionLabels[reason] : reason.replaceAll('_', ' ');
}
function recordedOmissions(omissions: SchemeCoverage['file_omissions'], expected: number | undefined) {
  if (!omissions || !Number.isSafeInteger(omissions.total) || omissions.total <= 0
    || (expected !== undefined && omissions.total !== expected) || !Array.isArray(omissions.files)
    || omissions.files.length === 0 || omissions.files.length > Math.min(omissions.total, 100)) return undefined;
  const paths = new Set<string>();
  for (const file of omissions.files) {
    if (typeof file?.path !== 'string' || !file.path.trim() || typeof file.reason !== 'string'
      || !file.reason.trim() || paths.has(file.path)) return undefined;
    paths.add(file.path);
  }
  return omissions;
}
const countRows: [keyof Omit<SchemeFileCounts, 'listing_complete'>, string][] = [
  ['repository_paths_total', 'Repository paths listed'], ['excluded_paths', 'Explicitly excluded paths'],
  ['safe_paths', 'Safe paths after exclusions'], ['eligible_files', 'Eligible candidate files'],
  ['policy_omitted_paths', 'Paths omitted by inspection policy'], ['attempted_files', 'Files attempted'],
  ['read_files', 'Files successfully read'], ['prefix_read_files', 'Prefix-only reads (subset of successful reads)'],
  ['failed_reads', 'Failed reads'], ['not_attempted_files', 'Eligible files not attempted'],
  ['evidence_files', 'Files in final evidence package'], ['read_not_in_evidence', 'Read files absent from final evidence'],
];
const policyRows: [keyof SchemeInspectionPolicy, string, string][] = [
  ['max_read_bytes', 'Read byte limit', 'bytes'], ['max_inspection_seconds', 'Inspection time limit', 'seconds'],
  ['max_file_bytes', 'Per-file read limit', 'bytes'], ['max_source_chars', 'Source excerpt input limit', 'characters'],
  ['max_package_chars', 'Evidence package limit', 'characters'], ['max_tree_paths', 'Tree path limit', 'paths'],
  ['max_metadata_bytes', 'Metadata byte limit', 'bytes'], ['max_metadata_seconds', 'Metadata time limit', 'seconds'],
];
export function SchemeInspectionDetails({ coverage, saved, current = false, embedded = false }: { coverage?: SchemeCoverage; saved: boolean; current?: boolean; embedded?: boolean }) {
  const counts = coverage?.file_counts;
  const policy = coverage?.inspection_policy;
  const limitations = Array.isArray(coverage?.limitations) ? coverage.limitations.filter((item): item is string => typeof item === 'string') : [];
  const eligible = counts?.eligible_files;
  const evidence = counts?.evidence_files;
  const knownTotal = counts?.listing_complete === true && nonnegative(eligible) && Number.isSafeInteger(eligible)
    && nonnegative(evidence) && Number.isSafeInteger(evidence) && nonnegative(counts.read_files)
    && Number.isSafeInteger(counts.read_files) && evidence <= counts.read_files && counts.read_files <= eligible;
  const omissions = recordedOmissions(coverage?.file_omissions, knownTotal ? eligible - evidence : undefined);
  const inputStatus = saved ? 'supplied' : 'selected';
  const content = <>
    <p>{saved ? 'Inspection recorded with this saved Scheme.' : current ? 'Fresh inspection of the repository; these counts do not describe the evidence used by the saved Scheme.' : 'Current repository inspection.'}</p>
    {counts && <>
      <p><strong>{saved ? 'AI input:' : 'Prepared for AI:'}</strong> {knownTotal ? `${evidence} of ${eligible} eligible repository files ${inputStatus}`
        : `${count(evidence)} files ${inputStatus}; the eligible total is not established`}.</p>
      <p><strong>Local reads:</strong> {count(counts.read_files)} files, including {count(counts.prefix_read_files)} prefix-only {counts.prefix_read_files === 1 ? 'read' : 'reads'}.
        {' '}{count(counts.read_not_in_evidence)} read files were left out of {saved ? 'the final AI input' : 'this evidence package'}.</p>
      {knownTotal && evidence < eligible && <p>{eligible - evidence} eligible files were not {inputStatus}.</p>}
      <p><small>Reading a file locally and supplying it to AI are different stages. Prefixes and selected excerpts do not cover the complete file; these counts do not establish correctness.</small></p>
    </>}
    <dl>
      <div><dt>Recorded conversation excerpts</dt><dd>{count(coverage?.conversation_excerpts)}</dd></div>
      {!counts && <div><dt>Recorded inspected files</dt><dd>{count(coverage?.files_inspected)}</dd></div>}
    </dl>
    {counts && typeof counts === 'object' ? <>
      <details><summary>File accounting and scope</summary>
      <dl>{countRows.map(([key, title]) => <div key={key}><dt>{title}</dt><dd>{count(counts[key])}</dd></div>)}
        <div><dt>Repository listing complete</dt><dd>{typeof counts.listing_complete === 'boolean' ? counts.listing_complete ? 'Yes' : 'No' : 'Not recorded'}</dd></div>
      </dl>
      <p>Repository paths are Git-listed tracked paths plus non-ignored untracked paths before explicit path exclusions. Tracked ignored paths contribute to exclusions; ignored untracked files are not counted. Unknown totals are shown as not recorded.</p>
      <p>The pipeline lists paths, applies exclusions and candidate policy, attempts reads, then selects final evidence. Successful reads are decoded files before parsing; prefix-only reads are part of that count, so do not add them again. Final evidence counts unique repository paths after all package limits.</p>
      </details>
    </> : <p>File counts were not recorded for {saved ? 'this saved Scheme' : 'this inspection'}.</p>}
    <p>Declared entrypoints and statically resolved local imports are prioritized; metadata, documentation, and code share the budget. Configured private-file, dependency-directory, and build-output exclusions are respected. Byte and time limits bound local inspection cost; source and package limits bound AI input and cost. These counts do not measure how much functionality the Scheme captures.</p>
    {policy && typeof policy === 'object' ? <details><summary>Recorded inspection limits</summary>
      <dl>{policyRows.map(([key, title, unit]) => <div key={key}><dt>{title}</dt><dd>{nonnegative(policy[key]) ? `${policy[key]} ${unit}` : 'Not recorded'}</dd></div>)}</dl>
    </details> : <p>Inspection limits were not recorded for {saved ? 'this saved Scheme' : 'this inspection'}.</p>}
    {coverage?.inspection_stopped && <p>Inspection stopped: {String(coverage.inspection_stopped).replaceAll('_', ' ')}</p>}
    {omissions && <details>
      <summary>Skipped files ({omissions.total})</summary>
      {omissions.total > omissions.files.length
        && <p>Showing {omissions.files.length} of {omissions.total} skipped files.</p>}
      <ul className="scheme-omitted-files">{omissions.files.map(({ path, reason }) =>
        <li key={path}><code>{path}</code> · {omissionLabel(reason)}</li>)}</ul>
    </details>}
    {saved && knownTotal && evidence < eligible && !omissions && <p><small>File names and individual reasons were not recorded for this version.</small></p>}
    {coverage?.omitted_reason_counts && typeof coverage.omitted_reason_counts === 'object'
      && !!Object.keys(coverage.omitted_reason_counts).length && <details><summary>Omission reasons</summary>
      <p><small>These totals count omitted evidence entries and inspection events. A file can contribute more than one excerpt.</small></p>
      <dl>{Object.entries(coverage.omitted_reason_counts).map(([reason, value]) =>
        <div key={reason}><dt>{omissionLabel(reason)}</dt><dd>{count(value)}</dd></div>)}</dl>
    </details>}
    {!!limitations.length && <details><summary>Inspection limitations</summary><ul>
      {limitations.map((item, index) => <li key={index}>{item}</li>)}
    </ul></details>}
  </>;
  return embedded ? content : <details className="scheme-inspection-details">
    <summary>{current ? 'Current repository inspection' : 'How ProjectHub inspected this repository'}</summary>
    {content}
  </details>;
}
export function SchemeInspectionTiming({ diagnostics, saved, embedded = false }: { diagnostics?: SchemeDiagnostics | null; saved: boolean; embedded?: boolean }) {
  if (!diagnostics || !['bytes_read', 'inspection_seconds', 'cache_hits', 'package_chars'].some(key => nonnegative(diagnostics[key as keyof SchemeDiagnostics]))) return null;
  const content = <>
    <p>{saved ? 'Recorded with this saved Scheme.' : 'Current repository inspection.'}</p>
    <dl>{([
      ['inspection_seconds', 'Inspection duration', 'seconds'], ['bytes_read', 'Bytes read', 'bytes'],
      ['cache_hits', 'Inspection cache hits', ''], ['package_chars', 'Evidence package size', 'characters'],
    ] as const).filter(([key]) => nonnegative(diagnostics[key])).map(([key, title, unit]) =>
      <div key={key}><dt>{title}</dt><dd>{diagnostics[key]} {unit}</dd></div>)}</dl>
  </>;
  return embedded ? content : <details><summary>Repository inspection diagnostics</summary>{content}</details>;
}

export function SchemeResultCoverage({ snapshot, current, showCurrent = false }: {
  snapshot?: (Inspection & { generated_at?: string | null; sources?: unknown[] }) | null;
  current?: Inspection & { sources?: unknown[] };
  showCurrent?: boolean;
}) {
  const inspection = schemeInspectionForDisplay(snapshot, current);
  return <CoverageDetails savedAt={snapshot?.generated_at} customCoverage
    note={inspection.saved ? undefined : 'Current inspection; no saved Scheme is selected.'}>
    <SchemeInspectionDetails coverage={inspection.coverage} saved={inspection.saved} embedded />
    <SchemeInspectionTiming diagnostics={inspection.diagnostics} saved={inspection.saved} />
    {showCurrent && inspection.saved && !inspection.coverage?.file_counts && current?.coverage?.file_counts &&
      <SchemeInspectionDetails coverage={current.coverage} saved={false} current />}
  </CoverageDetails>;
}
