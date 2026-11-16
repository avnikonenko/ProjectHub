import assert from 'node:assert/strict';
import test from 'node:test';
import { diagnosticCount, generationProfilePatch, parseGenerationDiagnostics, parseGenerationProfileSettings } from '../src/generationDiagnostics.ts';
import { parseKnowledgePreview } from '../src/knowledge.ts';
import { knowledgePreviewExport } from '../src/knowledgePreviewRecovery.ts';

function settings() {
  return { generation_profile: 'quality', configured_generation_profile: 'quality', generation_profiles: [
    { id: 'quality', label: 'Quality', description: 'Existing configuration.', window_chars: 14000, source_budget_chars: null, default_effort: null, experimental: false },
    { id: 'balanced', label: 'Balanced', description: 'Larger windows.', window_chars: 18000, source_budget_chars: null, default_effort: null, experimental: true },
    { id: 'economy', label: 'Economy', description: 'Partial coverage.', window_chars: 24000, source_budget_chars: 60000, default_effort: 'low', experimental: true },
  ] };
}
function rawPreview(diagnostics?: unknown) {
  return { published: false, publication_id: 'preview-1', knowledge: { project_id: 2, publication_id: 'preview-1', items: [], sources: [] },
    brief: { generated_at: '2026-11-14T00:00:00Z', project_state: { publication_id: 'preview-1' }, sections: { overview: [], current_status: [], architecture: [] }, sources: [], coverage: [] },
    ...(diagnostics === undefined ? {} : { diagnostics }) };
}
const complete = { total_tokens: 140, input_tokens: 100, output_tokens: 40, cached_input_tokens: 30, reasoning_output_tokens: 10, reported_requests: 2, unreported_requests: 0, status: 'complete' };

test('profile settings preserve trade-offs and effective/configured overrides; old worker-only servers still work', () => {
  const raw = settings(); raw.configured_generation_profile = 'economy';
  const before = structuredClone(raw); const parsed = parseGenerationProfileSettings(raw)!;
  assert.equal(parsed.generation_profile, 'quality');
  assert.equal(parsed.configured_generation_profile, 'economy');
  assert.equal(parsed.generation_profiles[2].source_budget_chars, 60000);
  assert.equal(parsed.generation_profiles[2].experimental, true);
  assert.equal(parseGenerationProfileSettings({ extraction_workers: 4 }), null);
  assert.deepEqual(raw, before);
});

test('malformed or missing profile settings fail without manufacturing a default', () => {
  for (const mutate of [
    (v: any) => { v.generation_profile = 'turbo'; }, (v: any) => { delete v.configured_generation_profile; },
    (v: any) => { v.generation_profiles = []; }, (v: any) => { v.generation_profiles.push(v.generation_profiles[0]); },
    (v: any) => { v.generation_profiles[0].window_chars = -1; }, (v: any) => { v.generation_profiles[2].source_budget_chars = true; },
  ]) { const raw = settings(); mutate(raw); assert.throws(() => parseGenerationProfileSettings(raw), /profiles/); }
});

test('changing a profile submits only that setting', () => {
  assert.deepEqual(generationProfilePatch('balanced'), { generation_profile: 'balanced' });
  assert.deepEqual(generationProfilePatch('economy'), { generation_profile: 'economy' });
  assert.throws(() => generationProfilePatch('other'), /supported/);
});

test('complete diagnostics preserve actual zeroes and token breakdowns without adding them', () => {
  const raw = { duration_seconds: 43.5, ai_requests: 0, cache_hits: 16, synthesis_requests: 1, model_requests: 2, token_usage: complete,
    profile: { id: 'quality', label: 'Quality', window_chars: 14000, source_budget_chars: null, requested_effort: 'medium', effective_effort: 'medium', notes: [] } };
  const before = structuredClone(raw); const result = parseGenerationDiagnostics(raw)!;
  assert.deepEqual(result, raw); assert.equal(result.token_usage!.total_tokens, 140); assert.deepEqual(raw, before);
  assert.equal(diagnosticCount(0), '0'); assert.equal(diagnosticCount(undefined), 'Unavailable'); assert.equal(diagnosticCount(null), 'Unavailable');
});

test('partial or unavailable usage never implies missing requests consumed zero tokens', () => {
  const partial = parseGenerationDiagnostics({ token_usage: { ...complete, reported_requests: 1, unreported_requests: 1, status: 'partial' } })!.token_usage!;
  assert.equal(partial.status, 'partial'); assert.equal(partial.total_tokens, 140); assert.equal(partial.unreported_requests, 1);
  assert.equal(parseGenerationDiagnostics({ token_usage: { ...complete, unreported_requests: 1 } })!.token_usage!.status, 'partial');
  const absent = parseGenerationDiagnostics({ token_usage: { status: 'unavailable' } })!.token_usage!;
  assert.equal(absent.status, 'unavailable'); assert.equal(absent.total_tokens, null); assert.equal(absent.reported_requests, null);
});

test('declared complete usage remains partial when measurements or reporting coverage are missing', () => {
  for (const field of ['total_tokens', 'input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_output_tokens', 'reported_requests', 'unreported_requests']) {
    const raw: Record<string, unknown> = { ...complete }; delete raw[field];
    const usage = parseGenerationDiagnostics({ token_usage: raw })!.token_usage!;
    assert.equal(usage.status, 'partial', field);
    if (field.endsWith('_tokens')) assert.equal(usage[field as keyof typeof usage], null);
  }
  const noReporting = parseGenerationDiagnostics({ token_usage: { ...complete, reported_requests: 0 } })!.token_usage!;
  assert.equal(noReporting.status, 'partial');
  const unavailable = parseGenerationDiagnostics({ token_usage: { ...complete, status: 'unavailable' } })!.token_usage!;
  assert.equal(unavailable.status, 'unavailable');
  assert.equal(unavailable.total_tokens, null); assert.equal(unavailable.input_tokens, null);
  assert.equal(unavailable.output_tokens, null); assert.equal(unavailable.cached_input_tokens, null);
  assert.equal(unavailable.reasoning_output_tokens, null);
});

test('declared complete usage cannot cover fewer or more requests than the measured run', () => {
  for (const requests of [1, 3]) {
    const usage = parseGenerationDiagnostics({ model_requests: requests, token_usage: complete })!.token_usage!;
    assert.equal(usage.status, 'partial');
    assert.equal(usage.total_tokens, 140);
  }
});

test('bad diagnostic values degrade safely without invalidating an otherwise legitimate preview', () => {
  const result = parseGenerationDiagnostics({ duration_seconds: Infinity, ai_requests: true, cache_hits: -1, model_requests: 0.5,
    token_usage: { input_tokens: '100', output_tokens: -1, total_tokens: true, status: 'complete' },
    profile: { notes: ['Useful note', { secret: 'not displayed' }], label: '<script>text only</script>' } })!;
  assert.equal(result.duration_seconds, undefined); assert.equal(result.cache_hits, undefined);
  assert.equal(result.token_usage!.status, 'unavailable'); assert.equal(result.token_usage!.input_tokens, null);
  assert.deepEqual(result.profile!.notes, ['Useful note']); assert.equal(result.profile!.label, '<script>text only</script>');
  assert.equal(parseGenerationDiagnostics(null), undefined); assert.equal(parseKnowledgePreview(rawPreview('malformed'), 2).diagnostics, undefined);
});

test('legacy previews work and downloaded JSON retains profile and usage diagnostics', () => {
  assert.equal(parseKnowledgePreview(rawPreview(), 2).diagnostics, undefined);
  const raw = rawPreview({ duration_seconds: 43.5, cache_hits: 16, model_requests: 2, token_usage: complete, profile: { id: 'economy', label: 'Economy', notes: ['Partial source coverage.'] } });
  const before = structuredClone(raw); const parsed = parseKnowledgePreview(raw, 2); const saved = JSON.parse(knowledgePreviewExport(parsed, 2).text);
  assert.deepEqual(saved.diagnostics, parsed.diagnostics); assert.equal(saved.diagnostics.profile.id, 'economy');
  assert.equal(saved.diagnostics.token_usage.total_tokens, 140); assert.equal(saved.published, false); assert.deepEqual(raw, before);
});

test('known batch timings and exact source-selection diagnostics survive preview parsing and export', () => {
  const diagnostics = { extraction_windows: 5, extraction_workers: 4, default_generation_configuration: false,
    batch_timings: [{ batch: 1, seconds: 0.001, cached: true }, { batch: 2, seconds: 43.5, cached: false }],
    source_selection: { available_primary_fragments: 33, selected_primary_fragments: 19, context_only_fragments: 1,
      omitted_primary_fragments: 14, selected_source_chars: 58700, partial: true },
    profile: { id: 'economy', label: 'Economy', experimental: true, notes: [] } };
  const raw = rawPreview(diagnostics); const before = structuredClone(raw);
  const parsed = parseKnowledgePreview(raw, 2);
  assert.deepEqual(parsed.diagnostics, diagnostics);
  assert.deepEqual(JSON.parse(knowledgePreviewExport(parsed, 2).text).diagnostics, diagnostics);
  assert.deepEqual(raw, before);
});

test('malformed batch and coverage diagnostics are omitted without making up zeroes', () => {
  const result = parseGenerationDiagnostics({ extraction_windows: true, extraction_workers: -1,
    default_generation_configuration: 'false', batch_timings: [null, { batch: 1, seconds: Infinity, cached: true },
      { batch: 2, seconds: 1, cached: 'true' }, { batch: 3, seconds: 0, cached: false }],
    source_selection: { available_primary_fragments: '33', selected_primary_fragments: -1,
      selected_source_chars: true, partial: 'false', context_only_fragments: 0 } })!;
  assert.equal(result.extraction_windows, undefined); assert.equal(result.extraction_workers, undefined);
  assert.equal(result.default_generation_configuration, undefined);
  assert.deepEqual(result.batch_timings, [{ batch: 3, seconds: 0, cached: false }]);
  assert.deepEqual(result.source_selection, { context_only_fragments: 0 });
});

test('staged overview diagnostics preserve processed inputs, real calls and reused stages through preview export', () => {
  const diagnostics = {
    model_requests: 4, synthesis_requests: 3, synthesis_cache_hits: 2,
    overview_selection: { available_items: 120, selected_items: 120, omitted_items: 0,
      partial: false, staged_synthesis: true, synthesis_requests: 3, synthesis_cache_hits: 2,
      synthesis_batches_total: 5, synthesis_batches_completed: 5, synthesis_levels: 2,
      full_input_chars: 350000, input_chars: 110000 },
  };
  const raw = rawPreview(diagnostics); const before = structuredClone(raw);
  const parsed = parseKnowledgePreview(raw, 2);
  assert.deepEqual(parsed.diagnostics, diagnostics);
  assert.deepEqual(JSON.parse(knowledgePreviewExport(parsed, 2).text).diagnostics, diagnostics);
  assert.equal(parsed.diagnostics!.overview_selection!.omitted_items, 0);
  assert.equal(parsed.diagnostics!.overview_selection!.partial, false);
  assert.equal(parsed.diagnostics!.model_requests, 4);
  assert.equal(parsed.diagnostics!.synthesis_requests, 3);
  assert.equal(parsed.diagnostics!.synthesis_cache_hits, 2);
  assert.deepEqual(raw, before);
});

test('malformed overview measurements are ignored without inventing counts or coercing staged flags', () => {
  const parsed = parseGenerationDiagnostics({ synthesis_cache_hits: -1,
    overview_selection: { available_items: '120', selected_items: true, omitted_items: -1,
      partial: 'false', staged_synthesis: 1, synthesis_requests: Infinity, synthesis_cache_hits: 0,
      synthesis_batches_total: 0.5, synthesis_batches_completed: Number.MAX_SAFE_INTEGER + 1,
      synthesis_levels: null, full_input_chars: NaN, input_chars: -100 } })!;
  assert.equal(parsed.synthesis_cache_hits, undefined);
  assert.deepEqual(parsed.overview_selection, { synthesis_cache_hits: 0 });
  for (const value of [null, [], 'staged'])
    assert.equal(parseGenerationDiagnostics({ overview_selection: value })!.overview_selection, undefined);
});

test('legacy bounded and single-pass overview diagnostics do not imply staged synthesis or full input', () => {
  const bounded = { overview_selection: { available_items: 80, selected_items: 35, omitted_items: 45, partial: true } };
  assert.deepEqual(parseGenerationDiagnostics(bounded), bounded);
  assert.equal(parseGenerationDiagnostics(bounded)!.overview_selection!.staged_synthesis, undefined);
  const singlePass = parseGenerationDiagnostics({ synthesis_requests: 1 })!;
  assert.equal(singlePass.synthesis_cache_hits, undefined);
  assert.equal(singlePass.overview_selection, undefined);
  assert.deepEqual(singlePass, { synthesis_requests: 1 });
});

const synthesisTiming = { level: 0, batch: 1, input_chars: 140000, seconds: 15.75,
  model_seconds: 15, cache_lookup_seconds: 0.05, local_seconds: 0.7, cached: false, completed: true };

test('parallel synthesis workers and exact batch timings survive preview parsing and JSON export', () => {
  const diagnostics = { overview_selection: { synthesis_workers: 4, synthesis_batch_timings: [synthesisTiming,
    { ...synthesisTiming, level: 1, batch: 1, seconds: 0, model_seconds: 0, cache_lookup_seconds: 0,
      local_seconds: 0, cached: true, completed: false }] } };
  const raw = rawPreview(diagnostics); const before = structuredClone(raw);
  const parsed = parseKnowledgePreview(raw, 2);
  assert.deepEqual(parsed.diagnostics, diagnostics);
  assert.deepEqual(JSON.parse(knowledgePreviewExport(parsed, 2).text).diagnostics, diagnostics);
  assert.deepEqual(raw, before);
});

test('malformed synthesis timings are ignored without invalidating a saved preview', () => {
  const invalid = [null, [], 'not timings', { ...synthesisTiming, level: -1 },
    { ...synthesisTiming, batch: 0 }, { ...synthesisTiming, input_chars: '140000' },
    { ...synthesisTiming, seconds: Infinity }, { ...synthesisTiming, model_seconds: -1 },
    { ...synthesisTiming, cache_lookup_seconds: true }, { ...synthesisTiming, local_seconds: NaN },
    { ...synthesisTiming, cached: 'false' }, { ...synthesisTiming, completed: 1 }];
  const parsed = parseKnowledgePreview(rawPreview({ overview_selection: {
    synthesis_workers: 0, synthesis_batch_timings: [...invalid, synthesisTiming],
  } }), 2);
  assert.equal(parsed.diagnostics!.overview_selection!.synthesis_workers, undefined);
  assert.deepEqual(parsed.diagnostics!.overview_selection!.synthesis_batch_timings, [synthesisTiming]);
  assert.equal(parseGenerationDiagnostics({ overview_selection: { synthesis_workers: true } })!.overview_selection!.synthesis_workers, undefined);
  assert.equal(parseGenerationDiagnostics({ overview_selection: { synthesis_batch_timings: 'invalid' } })!.overview_selection!.synthesis_batch_timings, undefined);
});

test('oversized synthesis timing lists are bounded and visibly marked through export', () => {
  const timings = Array.from({ length: 4097 }, (_, index) => ({ ...synthesisTiming, batch: index + 1 }));
  const raw = rawPreview({ overview_selection: { synthesis_workers: 2,
    synthesis_batch_timings: timings } });
  const parsed = parseKnowledgePreview(raw, 2);
  assert.equal(parsed.diagnostics!.overview_selection!.synthesis_batch_timings!.length, 4096);
  assert.equal(parsed.diagnostics!.overview_selection!.synthesis_batch_timings_truncated, true);
  const saved = JSON.parse(knowledgePreviewExport(parsed, 2).text);
  const restored = parseKnowledgePreview(saved, 2);
  assert.deepEqual(restored.diagnostics, parsed.diagnostics);
  assert.equal(timings.length, 4097);
});
