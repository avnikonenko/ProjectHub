import type { ReactNode } from 'react';
import { GenerationDetails } from './GenerationDetails';
import { parseGenerationDiagnostics } from './generationDiagnostics';
import type { GenerationDiagnostics } from './generationDiagnostics';

function InputCoverage({ measured, hasCustomCoverage }: { measured?: GenerationDiagnostics; hasCustomCoverage: boolean }) {
  const repo = measured?.evidence_coverage?.repository;
  const code = repo?.by_category?.code;
  const conversations = measured?.evidence_coverage?.conversations;
  const archive = measured?.archive_conversations;
  const selected = conversations?.selected_fragments ?? measured?.source_selection?.selected_primary_fragments;
  const available = conversations?.available_fragments ?? measured?.source_selection?.available_primary_fragments;
  const omitted = conversations?.omitted_fragments ?? measured?.source_selection?.omitted_primary_fragments;
  const validFragments = selected !== undefined && available !== undefined && selected <= available
    && (omitted === undefined || selected + omitted === available);
  const overview = measured?.overview_selection;
  const validItems = overview?.selected_items !== undefined && overview.available_items !== undefined
    && overview.selected_items <= overview.available_items;
  const fmt = (value: number) => value.toLocaleString('en-US');
  return <>
    {repo ? <section>
      <h4>Repository files</h4>
      {repo.status === 'not_used' ? <p>Code was not an input to these Knowledge records; they were extracted from conversations.</p>
        : repo.status === 'not_linked' ? <p>No repository was linked for this result.</p>
          : repo.status === 'unavailable' ? <p>Repository inspection was unavailable. File coverage is unknown.</p>
            : repo.status === 'unrecorded' ? <p>Exact repository file coverage was not recorded for this result.</p>
            : <>
              <p>{repo.ai_input_files === undefined ? 'The number of files supplied to AI was not recorded.'
                : repo.inventory_complete && repo.inventory_scope === 'git_tracked' && repo.total_files != null
                  ? `${fmt(repo.ai_input_files)} file excerpts supplied from ${fmt(repo.total_files)} tracked paths.`
                  : `${fmt(repo.ai_input_files)} file excerpts supplied; the complete tracked-file count is unknown.`}</p>
              {repo.inventory_complete && code?.read_files !== undefined && code.eligible_files !== undefined
                && <p>Code files: {fmt(code.read_files)} of {fmt(code.eligible_files)} eligible tracked code files read
                  {code.partial_files !== undefined ? `; ${fmt(code.partial_files)} only in part.` : '.'}</p>}
              <dl>
                {repo.eligible_files != null && <><dt>Eligible source, documentation and configuration files</dt><dd>{fmt(repo.eligible_files)}</dd></>}
                {repo.read_files !== undefined && <><dt>Files read locally</dt><dd>{fmt(repo.read_files)}</dd></>}
                {repo.full_files !== undefined && <><dt>Files with full text read</dt><dd>{fmt(repo.full_files)}</dd></>}
                {repo.partial_files !== undefined && <><dt>Files read only in part</dt><dd>{fmt(repo.partial_files)}</dd></>}
                {repo.not_selected_files !== undefined && <><dt>Eligible files not selected</dt><dd>{fmt(repo.not_selected_files)}</dd></>}
                {repo.excluded_files !== undefined && <><dt>Files excluded by inspection rules</dt><dd>{fmt(repo.excluded_files)}</dd></>}
                {repo.unsupported_files !== undefined && <><dt>Files outside supported inspection types</dt><dd>{fmt(repo.unsupported_files)}</dd></>}
                {repo.failed_files !== undefined && <><dt>Selected files that could not be read</dt><dd>{fmt(repo.failed_files)}</dd></>}
                {repo.empty_files !== undefined && <><dt>Selected files without usable text</dt><dd>{fmt(repo.empty_files)}</dd></>}
              </dl>
              <p><small>The inventory covers Git-tracked paths only. Untracked paths, including ignored untracked files, are outside this inventory. A supplied excerpt may contain only a prefix or selected declarations.</small></p>
            </>}
      {!!repo.notes.length && <ul>{repo.notes.map((note, index) => <li key={index}>{note}</li>)}</ul>}
      {!!repo.paths && Object.entries(repo.paths).some(([, paths]) => paths?.length) && <details>
        <summary>Files used and missed</summary>
        {(['read', 'partial', 'not_selected', 'failed', 'empty'] as const).map(key => !!repo.paths?.[key]?.length && <section key={key}>
          <h4>{{ read: 'Read files', partial: 'Partly read files', not_selected: 'Eligible files not selected', failed: 'Read failures', empty: 'Files without usable text' }[key]}</h4>
          <ul>{repo.paths[key]!.map((path, index) => <li key={index}><code>{path}</code></li>)}</ul>
        </section>)}
        {repo.paths_truncated && <p>File lists are shortened; the recorded counts above include the full inspected inventory.</p>}
      </details>}
    </section> : !hasCustomCoverage && <p>Exact repository file coverage was not recorded for this result.</p>}
    {(conversations || archive || validFragments) ? <section>
      <h4>Conversations</h4>
      {conversations?.known_sources !== undefined && conversations.processed_sources !== undefined
        && <p>{fmt(conversations.processed_sources)} of {fmt(conversations.known_sources)} saved conversation sources at least partly processed.</p>}
      {conversations && <dl>
        {conversations.partial_sources !== undefined && <><dt>Partially available sources</dt><dd>{fmt(conversations.partial_sources)}</dd></>}
        {conversations.excluded_sources !== undefined && <><dt>Excluded sources</dt><dd>{fmt(conversations.excluded_sources)}</dd></>}
        {conversations.unavailable_sources !== undefined && <><dt>Unavailable sources</dt><dd>{fmt(conversations.unavailable_sources)}</dd></>}
      </dl>}
      {archive && <>
        {archive.read_sessions !== undefined && archive.linked_sessions !== undefined
          && <p>{fmt(archive.read_sessions)} of {fmt(archive.linked_sessions)} linked Codex sessions read.</p>}
        <dl>
          {archive.unique_turns !== undefined && <><dt>Distinct finished turns included</dt><dd>{fmt(archive.unique_turns)}</dd></>}
          {archive.duplicate_turns_omitted !== undefined && <><dt>Repeated inherited turns collapsed</dt><dd>{fmt(archive.duplicate_turns_omitted)}</dd></>}
          {archive.active_turns_skipped !== undefined && <><dt>Unfinished turns omitted</dt><dd>{fmt(archive.active_turns_skipped)}</dd></>}
        </dl>
        {!!archive.notes.length && <ul>{archive.notes.map((note, index) => <li key={index}>{note}</li>)}</ul>}
      </>}
      {validFragments && <p>{fmt(selected)} of {fmt(available)} available conversation fragments selected for extraction
        {omitted !== undefined ? `; ${fmt(omitted)} omitted.` : '.'}</p>}
      {validFragments && <p><small>Fragments are pieces of saved messages, not separate conversations. This does not count remote or unlinked history.</small></p>}
      {!!conversations?.notes.length && <ul>{conversations.notes.map((note, index) => <li key={index}>{note}</li>)}</ul>}
    </section> : !hasCustomCoverage && <p>Exact conversation coverage was not recorded for this result.</p>}
    {validItems && <p>Summary input: {fmt(overview.selected_items!)} of {fmt(overview.available_items!)} eligible Knowledge records considered
      {overview.omitted_items !== undefined && overview.omitted_items <= overview.available_items!
        && overview.selected_items! + overview.omitted_items === overview.available_items
        ? `; ${fmt(overview.omitted_items)} omitted.` : '.'}</p>}
    <p><small>Coverage shows the available input, not whether every detail was retained or the conclusions are correct. Check source references for important claims.</small></p>
  </>;
}

type Props = {
  savedAt?: string | null;
  sourceCount?: number;
  coverage?: unknown;
  diagnostics?: unknown;
  children?: ReactNode;
  note?: string;
  customCoverage?: boolean;
};

function coverageGroups(raw: unknown): [string, string[]][] {
  if (Array.isArray(raw)) return [['Recorded coverage', raw.filter((value): value is string => typeof value === 'string')]];
  if (!raw || typeof raw !== 'object') return [];
  return Object.entries(raw).flatMap(([name, values]) => Array.isArray(values)
    ? [[name.replaceAll('_', ' '), values.filter((value): value is string => typeof value === 'string')] as [string, string[]]] : []);
}

/** Only metadata belonging to this saved result is supplied by callers. */
export function CoverageDetails({ savedAt, sourceCount, coverage, diagnostics, children, note, customCoverage = false }: Props) {
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
    <InputCoverage measured={measured} hasCustomCoverage={customCoverage} />
    {children}
    {!!groups.length && <details>
      <summary>Source notes and omissions</summary>
      {groups.map(([name, values], index) => <section key={`${name}:${index}`}>
        <h4>{name.charAt(0).toUpperCase() + name.slice(1)}</h4>
        <ul>{values.map((value, item) => <li key={item}>{value}</li>)}</ul>
      </section>)}
    </details>}
    {recordedSourceCount !== undefined && <p><small>Cited sources in this result: {recordedSourceCount}. Citation counts do not measure input coverage.</small></p>}
    {hasMeasurements && <details><summary>Processing diagnostics</summary>
      <p><small>Timing, workers and request counts describe processing cost, not evidence coverage.</small></p>
      <GenerationDetails diagnostics={measured} embedded />
    </details>}
  </details>;
}
