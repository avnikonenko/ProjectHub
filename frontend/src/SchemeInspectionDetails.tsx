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
export function SchemeInspectionDetails({ coverage, saved, current = false }: { coverage?: SchemeCoverage; saved: boolean; current?: boolean }) {
  const counts = coverage?.file_counts;
  const policy = coverage?.inspection_policy;
  const limitations = Array.isArray(coverage?.limitations) ? coverage.limitations.filter((item): item is string => typeof item === 'string') : [];
  return <details className="scheme-inspection-details">
    <summary>{current ? 'Current repository inspection' : 'How ProjectHub inspected this repository'}</summary>
    <p>{saved ? 'Inspection recorded with this saved Scheme.' : current ? 'Fresh inspection of the repository; these counts do not describe the evidence used by the saved Scheme.' : 'Current repository inspection.'}</p>
    {counts && typeof counts === 'object' ? <>
      <dl>{countRows.map(([key, title]) => <div key={key}><dt>{title}</dt><dd>{count(counts[key])}</dd></div>)}
        <div><dt>Repository listing complete</dt><dd>{typeof counts.listing_complete === 'boolean' ? counts.listing_complete ? 'Yes' : 'No' : 'Not recorded'}</dd></div>
      </dl>
      <p>Repository paths are Git-listed tracked paths plus non-ignored untracked paths before explicit path exclusions. Tracked ignored paths contribute to exclusions; ignored untracked files are not counted. Unknown totals are shown as not recorded.</p>
      <p>The pipeline lists paths, applies exclusions and candidate policy, attempts reads, then selects final evidence. Successful reads are decoded files before parsing; prefix-only reads are part of that count, so do not add them again. Final evidence counts unique repository paths after all package limits.</p>
    </> : <p>File counts were not recorded for {saved ? 'this saved Scheme' : 'this inspection'}.</p>}
    <p>Declared entrypoints and statically resolved local imports are prioritized; metadata, documentation, and code share the budget. Configured private-file, dependency-directory, and build-output exclusions are respected. Byte and time limits bound local inspection cost; source and package limits bound AI input and cost. These counts do not measure how much functionality the Scheme captures.</p>
    {policy && typeof policy === 'object' ? <details><summary>Recorded inspection limits</summary>
      <dl>{policyRows.map(([key, title, unit]) => <div key={key}><dt>{title}</dt><dd>{nonnegative(policy[key]) ? `${policy[key]} ${unit}` : 'Not recorded'}</dd></div>)}</dl>
    </details> : <p>Inspection limits were not recorded for {saved ? 'this saved Scheme' : 'this inspection'}.</p>}
    {coverage?.inspection_stopped && <p>Inspection stopped: {String(coverage.inspection_stopped).replaceAll('_', ' ')}</p>}
    {coverage?.omitted_reason_counts && typeof coverage.omitted_reason_counts === 'object' && <details><summary>Omission reasons</summary><dl>
      {Object.entries(coverage.omitted_reason_counts).map(([reason, value]) => <div key={reason}><dt>{reason.replaceAll('_', ' ')}</dt><dd>{count(value)}</dd></div>)}
    </dl></details>}
    {!!limitations.length && <details><summary>Inspection limitations</summary><ul>
      {limitations.map((item, index) => <li key={index}>{item}</li>)}
    </ul></details>}
  </details>;
}
export function SchemeInspectionTiming({ diagnostics, saved }: { diagnostics?: SchemeDiagnostics | null; saved: boolean }) {
  if (!diagnostics || !['bytes_read', 'inspection_seconds', 'cache_hits', 'package_chars'].some(key => nonnegative(diagnostics[key as keyof SchemeDiagnostics]))) return null;
  return <details><summary>Repository inspection diagnostics</summary>
    <p>{saved ? 'Recorded with this saved Scheme.' : 'Current repository inspection.'}</p>
    <dl>{([
      ['inspection_seconds', 'Inspection duration', 'seconds'], ['bytes_read', 'Bytes read', 'bytes'],
      ['cache_hits', 'Inspection cache hits', ''], ['package_chars', 'Evidence package size', 'characters'],
    ] as const).filter(([key]) => nonnegative(diagnostics[key])).map(([key, title, unit]) =>
      <div key={key}><dt>{title}</dt><dd>{diagnostics[key]} {unit}</dd></div>)}</dl>
  </details>;
}
