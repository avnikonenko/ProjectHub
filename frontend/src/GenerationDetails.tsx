import type { GenerationDiagnostics } from './generationDiagnostics';
import { diagnosticCount } from './generationDiagnostics';

export function GenerationDetails({ diagnostics }: { diagnostics?: GenerationDiagnostics }) {
  if (!diagnostics) return null;
  const usage = diagnostics.token_usage;
  const profile = diagnostics.profile;
  const selection = diagnostics.source_selection;
  const overview = diagnostics.overview_selection;
  const overviewCoverageAvailable = overview?.selected_items !== undefined
    && overview.available_items !== undefined && overview.selected_items <= overview.available_items;
  const overviewStagesAvailable = overview?.synthesis_batches_completed !== undefined
    && overview.synthesis_batches_total !== undefined
    && overview.synthesis_batches_completed <= overview.synthesis_batches_total;
  const coverageAvailable = selection?.selected_primary_fragments !== undefined
    && selection.available_primary_fragments !== undefined
    && selection.selected_primary_fragments <= selection.available_primary_fragments;
  return <details className="generation-details">
    <summary>Generation details</summary>
    <dl>
      <dt>Profile</dt><dd>{profile?.label || profile?.id || 'Unavailable for this saved result'}</dd>
      <dt>Duration</dt><dd>{diagnostics.duration_seconds === undefined ? 'Unavailable' : `${diagnostics.duration_seconds.toFixed(1)} seconds`}</dd>
      <dt>Model requests</dt><dd>{diagnosticCount(diagnostics.model_requests)}</dd>
      <dt>Extraction cache hits</dt><dd>{diagnosticCount(diagnostics.cache_hits)}</dd>
      <dt>Fresh extraction requests</dt><dd>{diagnosticCount(diagnostics.ai_requests)}</dd>
      <dt>Synthesis model requests</dt><dd>{diagnosticCount(diagnostics.synthesis_requests ?? overview?.synthesis_requests)}</dd>
      {(diagnostics.synthesis_cache_hits !== undefined || overview?.synthesis_cache_hits !== undefined) && <>
        <dt>Reused synthesis stages</dt><dd>{diagnosticCount(diagnostics.synthesis_cache_hits ?? overview?.synthesis_cache_hits)}</dd>
      </>}
      {profile?.effective_effort && <><dt>Effective reasoning effort</dt><dd>{profile.effective_effort}</dd></>}
    </dl>
    {coverageAvailable && <p>Extraction coverage: {diagnosticCount(selection.selected_primary_fragments)} of {diagnosticCount(selection.available_primary_fragments)} primary fragments
      {(selection.partial || selection.selected_primary_fragments! < selection.available_primary_fragments!) && ' · Partial coverage'}</p>}
    {overview?.staged_synthesis && <p>Overview synthesis: summarized in stages, then combined.</p>}
    {overviewCoverageAvailable && <p>Overview input: {diagnosticCount(overview.selected_items)} of {diagnosticCount(overview.available_items)} active knowledge items processed
      {(overview.partial || overview.selected_items! < overview.available_items!) && ' · Partial input'}</p>}
    {overviewStagesAvailable && <p>Synthesis stages completed: {diagnosticCount(overview.synthesis_batches_completed)} of {diagnosticCount(overview.synthesis_batches_total)}</p>}
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
  </details>;
}
