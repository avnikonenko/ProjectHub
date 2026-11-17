import type { GenerationDiagnostics } from './generationDiagnostics';
import { diagnosticCount } from './generationDiagnostics';

export function GenerationDetails({ diagnostics, embedded = false }: { diagnostics?: GenerationDiagnostics; embedded?: boolean }) {
  if (!diagnostics) return null;
  const usage = diagnostics.token_usage;
  const profile = diagnostics.profile;
  const selection = diagnostics.source_selection;
  const overview = diagnostics.overview_selection;
  const synthesisTimings = overview?.synthesis_batch_timings;
  const overviewCoverageAvailable = overview?.selected_items !== undefined
    && overview.available_items !== undefined && overview.selected_items <= overview.available_items
    && (overview.omitted_items === undefined || overview.selected_items + overview.omitted_items === overview.available_items);
  const overviewStagesAvailable = overview?.synthesis_batches_completed !== undefined
    && overview.synthesis_batches_total !== undefined
    && overview.synthesis_batches_completed <= overview.synthesis_batches_total;
  const coverageAvailable = selection?.selected_primary_fragments !== undefined
    && selection.available_primary_fragments !== undefined
    && selection.selected_primary_fragments <= selection.available_primary_fragments
    && (selection.omitted_primary_fragments === undefined
      || selection.selected_primary_fragments + selection.omitted_primary_fragments === selection.available_primary_fragments);
  const content = <>
    <dl>
      <dt>Profile</dt><dd>{profile?.label || profile?.id || 'Unavailable for this saved result'}</dd>
      <dt>Duration</dt><dd>{diagnostics.duration_seconds === undefined ? 'Unavailable' : `${diagnostics.duration_seconds.toFixed(1)} seconds`}</dd>
      {diagnostics.input_records !== undefined && <><dt>Input records</dt><dd>{diagnosticCount(diagnostics.input_records)}</dd></>}
      {diagnostics.input_chars !== undefined && <><dt>Input text characters</dt><dd>{diagnosticCount(diagnostics.input_chars)}</dd></>}
      {diagnostics.archive_generated_batches !== undefined && <><dt>Fresh conversation summary batches</dt><dd>{diagnosticCount(diagnostics.archive_generated_batches)}</dd></>}
      {diagnostics.archive_cache_hits !== undefined && <><dt>Reused conversation summary batches</dt><dd>{diagnosticCount(diagnostics.archive_cache_hits)}</dd></>}
      {diagnostics.extraction_windows !== undefined && <><dt>Extraction batches</dt><dd>{diagnosticCount(diagnostics.extraction_windows)}</dd></>}
      {diagnostics.extraction_workers !== undefined && <><dt>Extraction workers</dt><dd>{diagnosticCount(diagnostics.extraction_workers)}</dd></>}
      {diagnostics.archive_workers !== undefined && <><dt>Conversation summary workers</dt><dd>{diagnosticCount(diagnostics.archive_workers)}</dd></>}
      <dt>Model requests</dt><dd>{diagnosticCount(diagnostics.model_requests)}</dd>
      <dt>Extraction cache hits</dt><dd>{diagnosticCount(diagnostics.cache_hits)}</dd>
      <dt>Fresh extraction requests</dt><dd>{diagnosticCount(diagnostics.ai_requests)}</dd>
      <dt>Synthesis model requests</dt><dd>{diagnosticCount(diagnostics.synthesis_requests ?? overview?.synthesis_requests)}</dd>
      {(diagnostics.synthesis_cache_hits !== undefined || overview?.synthesis_cache_hits !== undefined) && <>
        <dt>Reused synthesis stages</dt><dd>{diagnosticCount(diagnostics.synthesis_cache_hits ?? overview?.synthesis_cache_hits)}</dd>
      </>}
      {overview?.synthesis_workers !== undefined && <>
        <dt>Parallel synthesis workers</dt><dd>{diagnosticCount(overview.synthesis_workers)}</dd>
      </>}
      {profile?.effective_effort && <><dt>Effective reasoning effort</dt><dd>{profile.effective_effort}</dd></>}
    </dl>
    {diagnostics.input_chars !== undefined && <p><small>Input text counts exclude instructions, schemas and repeated requests.</small></p>}
    {coverageAvailable && <p>Extraction coverage: {diagnosticCount(selection.selected_primary_fragments)} of {diagnosticCount(selection.available_primary_fragments)} primary fragments
      {(selection.partial || selection.selected_primary_fragments! < selection.available_primary_fragments!) && ' · Partial coverage'}</p>}
    {overview?.staged_synthesis && <p>Overview synthesis: summarized in stages, then combined.</p>}
    {overviewCoverageAvailable && <p>Overview input: {diagnosticCount(overview.selected_items)} of {diagnosticCount(overview.available_items)} active knowledge items processed
      {(overview.partial || overview.selected_items! < overview.available_items!) && ' · Partial input'}</p>}
    {overviewStagesAvailable && <p>Synthesis stages completed: {diagnosticCount(overview.synthesis_batches_completed)} of {diagnosticCount(overview.synthesis_batches_total)}</p>}
    {!!synthesisTimings?.length && <details>
      <summary>Synthesis batch timings</summary>
      <p><small>Batch times can overlap; do not add them to the total duration.</small></p>
      <div className="scheme-table-scroll" role="region" aria-label="Synthesis batch timings table" tabIndex={0}><table className="diagnostic-table batch-timing-table">
        <thead><tr><th scope="col">Level</th><th scope="col">Batch</th><th scope="col">Input characters</th><th scope="col">Total (s)</th><th scope="col">Model (s)</th><th scope="col">Cache lookup (s)</th><th scope="col">Local work (s)</th><th scope="col">Result</th></tr></thead>
        <tbody>{synthesisTimings.slice(0, 100).map((timing, index) => <tr key={index}>
          <td>{timing.level}</td><td>{timing.batch}</td><td>{diagnosticCount(timing.input_chars)}</td>
          <td>{timing.seconds.toFixed(2)}</td><td>{timing.model_seconds.toFixed(2)}</td>
          <td>{timing.cache_lookup_seconds.toFixed(2)}</td><td>{timing.local_seconds.toFixed(2)}</td>
          <td>{timing.completed ? timing.cached ? 'Reused' : 'Completed' : 'Incomplete'}</td>
        </tr>)}</tbody>
      </table></div>
      {synthesisTimings.length > 100 && <p><small>Showing the first 100 of {diagnosticCount(synthesisTimings.length)} recorded batch timings.</small></p>}
      {overview?.synthesis_batch_timings_truncated && <p><small>Some timing records were omitted because the result was too large.</small></p>}
    </details>}
    {overviewCoverageAvailable && <p><small>Processed input does not mean every item appears in the final overview.</small></p>}
    {!!profile?.notes.length && <ul>{profile.notes.map((note, index) => <li key={index}>{note}</li>)}</ul>}
    {!usage || usage.status === 'unavailable' ? <p>Token usage is unavailable for this run.</p> : <>
      <p>{usage.status === 'partial' ? 'Partial reported token usage' : 'Reported token usage'}
        {usage.reported_requests !== null && ` · ${diagnosticCount(usage.reported_requests)} requests reported`}
        {usage.unreported_requests !== null && usage.unreported_requests > 0 && ` · ${diagnosticCount(usage.unreported_requests)} unreported`}</p>
      <dl>
        <dt>Total tokens</dt><dd>{diagnosticCount(usage.total_tokens)}</dd>
        <dt>Input tokens</dt><dd>{diagnosticCount(usage.input_tokens)}</dd>
        <dt>Output tokens</dt><dd>{diagnosticCount(usage.output_tokens)}</dd>
        <dt>Cached input tokens</dt><dd>{diagnosticCount(usage.cached_input_tokens)}</dd>
        <dt>Reasoning output tokens</dt><dd>{diagnosticCount(usage.reasoning_output_tokens)}</dd>
      </dl>
      <p><small>Counts are reported by Codex. Partial totals do not include unreported requests. Cached input and reasoning tokens are breakdowns, not extra tokens to add to the total.</small></p>
    </>}
  </>;
  return embedded ? content : <details className="generation-details"><summary>Generation details</summary>{content}</details>;
}
