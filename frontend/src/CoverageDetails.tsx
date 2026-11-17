import type { ReactNode } from 'react';
import { GenerationDetails } from './GenerationDetails';
import { parseGenerationDiagnostics } from './generationDiagnostics';
import type { GenerationDiagnostics } from './generationDiagnostics';

const omissionLabels: Record<string, string> = {
  byte_limit: 'Local read size limit', character_limit: 'AI text length limit',
  declaration_selection: 'Only selected declarations supplied', line_width_limit: 'Long lines shortened',
  excerpt_limit: 'Excerpt size limit', utf8_boundary: 'Read ended within a text character',
  parent_limit_linked_sessions: 'Linked sessions omitted by source-count limit',
  parent_limit_imported_chats: 'Imported chats omitted by source-count limit',
  unlinked_sessions: 'Unlinked sessions excluded', active_turns: 'Unfinished turns omitted',
  oversized_turns: 'Oversized turns omitted', oversized_documents: 'Oversized documents omitted',
  global_text_budget_turns: 'Turns omitted by total text budget',
  global_text_budget_documents: 'Documents omitted by total text budget',
  global_text_budget_messages: 'Messages omitted by total text budget',
  global_message_budget_turns: 'Turns omitted by message-count limit',
  global_message_budget_messages: 'Messages omitted by message-count limit',
  invalid_turns: 'Turns with unreadable or invalid content', invalid_documents: 'Unreadable or invalid documents omitted',
  empty_sessions: 'Sessions without usable messages', turn_limit_turns: 'Turns omitted by per-session limit',
};
function omissionLabel(value: string) {
  return Object.prototype.hasOwnProperty.call(omissionLabels, value) ? omissionLabels[value] : value.replaceAll('_', ' ');
}

function uniqueNotes(notes: string[]) {
  const seen = new Set<string>();
  return notes.filter(note => {
    const key = note.trim().replace(/\s+/g, ' ');
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function InputCoverage({ measured, hasCustomCoverage }: { measured?: GenerationDiagnostics; hasCustomCoverage: boolean }) {
  const repo = measured?.evidence_coverage?.repository;
  const code = repo?.by_category?.code;
  const conversations = measured?.evidence_coverage?.conversations;
  const inventory = conversations?.source_inventory;
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
  const detailedPaths = new Set(repo?.file_details?.map(detail => detail.path) ?? []);
  const fallbackPaths = (key: 'read' | 'partial' | 'not_selected' | 'failed' | 'empty') =>
    repo?.paths?.[key]?.filter(path => !(key === 'read' || key === 'partial') || !detailedPaths.has(path)) ?? [];
  return <>
    {(conversations || archive || validFragments) ? <section>
      <h4>Conversation evidence</h4>
      {inventory ? <>
        {!(inventory.linked_codex_sessions_considered !== undefined && inventory.linked_codex_sessions_total !== undefined)
          && !(inventory.imported_chats_considered !== undefined && inventory.imported_chats_total !== undefined)
          && !(inventory.document_notes_considered !== undefined && inventory.document_notes_total !== undefined)
          && <p>Complete registered-source totals were not recorded for this result.</p>}
        {inventory.linked_codex_sessions_considered !== undefined && inventory.linked_codex_sessions_total !== undefined
          && <p>{fmt(inventory.linked_codex_sessions_considered)} of {fmt(inventory.linked_codex_sessions_total)} linked Codex sessions considered
            {inventory.linked_codex_sessions_omitted_parent_limit !== undefined
              ? `; ${fmt(inventory.linked_codex_sessions_omitted_parent_limit)} omitted by the source-count limit.` : '.'}</p>}
        {inventory.imported_chats_considered !== undefined && inventory.imported_chats_total !== undefined
          && <p>{fmt(inventory.imported_chats_considered)} of {fmt(inventory.imported_chats_total)} imported chats considered
            {inventory.imported_chats_omitted_parent_limit !== undefined
              ? `; ${fmt(inventory.imported_chats_omitted_parent_limit)} omitted by the source-count limit.` : '.'}</p>}
        {inventory.document_notes_considered !== undefined && inventory.document_notes_total !== undefined
          && <p>{fmt(inventory.document_notes_considered)} of {fmt(inventory.document_notes_total)} stored documents considered, including imported chats.</p>}
        <p><small>Considered sources may still have omitted messages. These totals cover sources registered in this project, not remote or unlinked conversations.</small></p>
        {!!inventory.omission_reasons && Object.values(inventory.omission_reasons).some(value => value > 0) && <details>
          <summary>Conversation omission reasons</summary>
          <p><small>Counts have different units—sessions, chats, turns, messages or documents—and must not be added together.</small></p>
          <dl>{Object.entries(inventory.omission_reasons).filter(([, value]) => value > 0).map(([reason, value]) =>
            <div key={reason}><dt>{omissionLabel(reason)}</dt><dd>{fmt(value)}</dd></div>)}</dl>
        </details>}
        {!!inventory.notes.length && <details><summary>Source selection details</summary>
          <ul>{uniqueNotes(inventory.notes).map((note, index) => <li key={index}>{note}</li>)}</ul>
        </details>}
      </> : conversations && <p>Complete registered-source totals were not recorded for this result.</p>}
      {conversations?.known_sources !== undefined && conversations.processed_sources !== undefined
        && <p>Within the saved source manifest, {fmt(conversations.processed_sources)} of {fmt(conversations.known_sources)} source groups were at least partly processed.</p>}
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
        {!!archive.notes.length && <details><summary>History reading details</summary>
          <ul>{uniqueNotes(archive.notes).map((note, index) => <li key={index}>{note}</li>)}</ul>
        </details>}
      </>}
      {validFragments && <p>{fmt(selected)} of {fmt(available)} available conversation fragments selected for extraction
        {omitted !== undefined ? `; ${fmt(omitted)} omitted.` : '.'}</p>}
      {validFragments && <p><small>Fragments are pieces of saved messages, not separate conversations. This does not count remote or unlinked history.</small></p>}
      {!!conversations?.notes.length && <details><summary>Conversation source details</summary>
        <ul>{uniqueNotes(conversations.notes).map((note, index) => <li key={index}>{note}</li>)}</ul>
      </details>}
    </section> : !hasCustomCoverage && <p>Exact conversation coverage was not recorded for this result.</p>}
    {repo ? <section>
      <h4>Targeted code inspection</h4>
      {repo.status === 'available' && <p><small>Selected code provides supporting context. This is not a full code audit.</small></p>}
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
                  {code.partial_files !== undefined ? `; ${fmt(code.partial_files)} supplied only in part.` : '.'}</p>}
              <dl>
                {repo.eligible_files != null && <><dt>Eligible source, documentation and configuration files</dt><dd>{fmt(repo.eligible_files)}</dd></>}
                {repo.read_files !== undefined && <><dt>Files read locally</dt><dd>{fmt(repo.read_files)}</dd></>}
                {repo.full_files !== undefined && <><dt>Files supplied in full</dt><dd>{fmt(repo.full_files)}</dd></>}
                {repo.partial_files !== undefined && <><dt>Files supplied only in part</dt><dd>{fmt(repo.partial_files)}</dd></>}
                {repo.not_selected_files !== undefined && <><dt>Eligible files not selected</dt><dd>{fmt(repo.not_selected_files)}</dd></>}
                {repo.excluded_files !== undefined && <><dt>Files excluded by inspection rules</dt><dd>{fmt(repo.excluded_files)}</dd></>}
                {repo.unsupported_files !== undefined && <><dt>Files outside supported inspection types</dt><dd>{fmt(repo.unsupported_files)}</dd></>}
                {repo.failed_files !== undefined && <><dt>Selected files that could not be read</dt><dd>{fmt(repo.failed_files)}</dd></>}
                {repo.empty_files !== undefined && <><dt>Selected files without usable text</dt><dd>{fmt(repo.empty_files)}</dd></>}
              </dl>
              <p><small>The inventory covers Git-tracked paths only. Untracked paths, including ignored untracked files, are outside this inventory. A supplied excerpt may contain only a prefix or selected declarations.</small></p>
            </>}
      {!!repo.notes.length && <details><summary>Inspection scope and limits</summary>
        <ul>{uniqueNotes(repo.notes).map((note, index) => <li key={index}>{note}</li>)}</ul>
      </details>}
      {(!!repo.file_details?.length || (!!repo.paths && Object.entries(repo.paths).some(([, paths]) => paths?.length))) && <details>
        <summary>Files used and missed</summary>
        {repo.file_details?.length ? <>
          <p><small>A file can be read fully on disk while only selected declarations reach the AI. Bytes and characters are different units; these counts are not a percentage of understanding.</small></p>
          <div className="scheme-table-scroll"><table>
            <caption>Local file reads and supplied AI excerpts</caption>
            <thead><tr><th>File</th><th>File size (bytes)</th><th>Read locally (bytes)</th><th>Local read</th><th>AI excerpt (characters)</th><th>AI excerpt</th><th>Why content was omitted</th></tr></thead>
            <tbody>{repo.file_details.map(detail => <tr key={detail.path}>
              <td><code>{detail.path}</code></td><td>{fmt(detail.file_bytes)}</td><td>{fmt(detail.bytes_read)}</td>
              <td>{detail.local_read_complete ? 'Complete' : 'Partial'}</td><td>{fmt(detail.excerpt_chars)}</td>
              <td>{detail.excerpt_complete ? 'Complete' : 'Partial'}</td>
              <td>{detail.omission_reasons.length ? detail.omission_reasons.map(omissionLabel).join('; ')
                : detail.excerpt_complete ? 'No source text omitted' : 'Reason not recorded'}</td>
            </tr>)}</tbody>
          </table></div>
          {repo.file_details_truncated && <p>Per-file details are shortened; the aggregate counts above are unchanged.</p>}
        </> : <p>Per-file read sizes and AI excerpt lengths were not recorded for this result.</p>}
        {(['read', 'partial', 'not_selected', 'failed', 'empty'] as const).map(key =>
          !!fallbackPaths(key).length && <section key={key}>
          <h4>{{ read: 'Read files', partial: 'Partly supplied files', not_selected: 'Eligible files not selected', failed: 'Read failures', empty: 'Files without usable text' }[key]}</h4>
          <ul>{fallbackPaths(key).map((path, index) => <li key={index}><code>{path}</code></li>)}</ul>
        </section>)}
        {repo.paths_truncated && <p>File lists are shortened; the recorded counts above include the full inspected inventory.</p>}
      </details>}
    </section> : !hasCustomCoverage && <p>Exact repository file coverage was not recorded for this result.</p>}
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
