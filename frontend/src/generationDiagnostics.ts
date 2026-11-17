import { parseArchiveConversations, parseEvidenceCoverage, type ArchiveConversations, type EvidenceCoverage } from './evidenceCoverage.ts';

export type GenerationProfileId = 'quality' | 'balanced' | 'economy';
export type GenerationProfileOption = {
  id: GenerationProfileId; label: string; description: string;
  window_chars: number; source_budget_chars: number | null;
  default_effort: string | null; experimental: boolean;
};
export type GenerationProfileSettings = {
  generation_profile: GenerationProfileId;
  configured_generation_profile: GenerationProfileId;
  generation_profiles: GenerationProfileOption[];
};
export type SynthesisBatchTiming = {
  level: number; batch: number; input_chars: number;
  seconds: number; model_seconds: number; cache_lookup_seconds: number; local_seconds: number;
  cached: boolean; completed: boolean;
};
const MAX_SYNTHESIS_TIMINGS = 4096;
export type GenerationDiagnostics = {
  evidence_coverage?: EvidenceCoverage;
  archive_conversations?: ArchiveConversations;
  duration_seconds?: number; ai_requests?: number; cache_hits?: number;
  input_records?: number; input_chars?: number; cited_source_count?: number;
  archive_workers?: number; archive_generated_batches?: number; archive_cache_hits?: number;
  synthesis_requests?: number; synthesis_cache_hits?: number; model_requests?: number;
  extraction_windows?: number; extraction_workers?: number; default_generation_configuration?: boolean;
  batch_timings?: { batch: number; seconds: number; cached: boolean }[];
  source_selection?: {
    available_primary_fragments?: number; selected_primary_fragments?: number;
    context_only_fragments?: number; omitted_primary_fragments?: number;
    selected_source_chars?: number; partial?: boolean;
  };
  overview_selection?: {
    available_items?: number; selected_items?: number; omitted_items?: number;
    partial?: boolean; staged_synthesis?: boolean;
    synthesis_requests?: number; synthesis_cache_hits?: number;
    synthesis_batches_total?: number; synthesis_batches_completed?: number;
    synthesis_levels?: number; full_input_chars?: number; input_chars?: number;
    synthesis_workers?: number; synthesis_batch_timings?: SynthesisBatchTiming[];
    synthesis_batch_timings_truncated?: boolean;
  };
  profile?: {
    id?: string; label?: string; window_chars?: number; source_budget_chars?: number | null;
    requested_effort?: string | null; effective_effort?: string | null; experimental?: boolean; notes: string[];
  };
  token_usage?: {
    total_tokens: number | null; input_tokens: number | null; output_tokens: number | null;
    cached_input_tokens: number | null; reasoning_output_tokens: number | null;
    reported_requests: number | null; unreported_requests: number | null;
    status: 'complete' | 'partial' | 'unavailable';
  };
};
const object = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);
const count = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const text = (value: unknown): value is string => typeof value === 'string' && value.length <= 500;
const profileId = (value: unknown): value is GenerationProfileId =>
  value === 'quality' || value === 'balanced' || value === 'economy';
const seconds = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0;

function synthesisTiming(raw: unknown): SynthesisBatchTiming[] {
  if (!object(raw) || !count(raw.level) || !count(raw.batch) || raw.batch < 1 || !count(raw.input_chars)
      || !seconds(raw.seconds) || !seconds(raw.model_seconds) || !seconds(raw.cache_lookup_seconds)
      || !seconds(raw.local_seconds) || typeof raw.cached !== 'boolean' || typeof raw.completed !== 'boolean') return [];
  return [{ level: raw.level, batch: raw.batch, input_chars: raw.input_chars,
    seconds: raw.seconds, model_seconds: raw.model_seconds, cache_lookup_seconds: raw.cache_lookup_seconds,
    local_seconds: raw.local_seconds, cached: raw.cached, completed: raw.completed }];
}

/** Old worker-only servers have no profile fields; partial/malformed contracts fail visibly. */
export function parseGenerationProfileSettings(raw: unknown): GenerationProfileSettings | null {
  if (!object(raw)) throw new Error('AI settings returned invalid generation profiles.');
  const fields = ['generation_profile', 'configured_generation_profile', 'generation_profiles'];
  if (fields.every((field) => !(field in raw))) return null;
  if (!profileId(raw.generation_profile) || !profileId(raw.configured_generation_profile)
      || !Array.isArray(raw.generation_profiles))
    throw new Error('AI settings returned invalid generation profiles.');
  const seen = new Set<string>();
  const profiles = raw.generation_profiles.map((value): GenerationProfileOption => {
    if (!object(value) || !profileId(value.id) || seen.has(value.id)
        || !text(value.label) || !value.label.trim() || !text(value.description)
        || !count(value.window_chars) || value.window_chars < 1000
        || (value.source_budget_chars !== null && (!count(value.source_budget_chars) || value.source_budget_chars < 1))
        || (value.default_effort !== null && !text(value.default_effort))
        || typeof value.experimental !== 'boolean')
      throw new Error('AI settings returned invalid generation profiles.');
    seen.add(value.id);
    return { id: value.id, label: value.label, description: value.description,
      window_chars: value.window_chars, source_budget_chars: value.source_budget_chars as number | null,
      default_effort: value.default_effort as string | null, experimental: value.experimental };
  });
  if (!seen.has(raw.generation_profile) || !seen.has(raw.configured_generation_profile))
    throw new Error('AI settings returned unavailable generation profiles.');
  return { generation_profile: raw.generation_profile, configured_generation_profile: raw.configured_generation_profile,
    generation_profiles: profiles };
}

export function generationProfilePatch(id: unknown) {
  if (!profileId(id)) throw new Error('Choose a supported generation profile.');
  return { generation_profile: id };
}

export function parseSchemeSourceSettings(raw: unknown) {
  if (!object(raw)) throw new Error('AI settings returned an invalid Scheme excerpt budget.');
  const fields = ['scheme_source_chars', 'configured_scheme_source_chars', 'scheme_source_chars_options'];
  if (fields.every(field => !(field in raw))) return null;
  const options = raw.scheme_source_chars_options;
  const value = raw.scheme_source_chars;
  const configured = raw.configured_scheme_source_chars;
  if (!Array.isArray(options) || options.length !== 3
    || ![40_000, 60_000, 80_000].every(option => options.includes(option))
    || !count(value) || !options.includes(value) || !count(configured) || !options.includes(configured)) {
    throw new Error('AI settings returned an unsupported Scheme excerpt budget.');
  }
  return { value, configured, options: options as number[] };
}

/** Diagnostics never invalidate an otherwise valid legacy preview or invent unavailable measurements. */
export function parseGenerationDiagnostics(raw: unknown): GenerationDiagnostics | undefined {
  if (!object(raw)) return undefined;
  const result: GenerationDiagnostics = {};
  const evidence = parseEvidenceCoverage(raw.evidence_coverage);
  if (evidence) result.evidence_coverage = evidence;
  const conversations = parseArchiveConversations(raw.archive_conversations);
  if (conversations) result.archive_conversations = conversations;
  if (typeof raw.duration_seconds === 'number' && Number.isFinite(raw.duration_seconds) && raw.duration_seconds >= 0)
    result.duration_seconds = raw.duration_seconds;
  for (const field of ['ai_requests', 'cache_hits', 'synthesis_requests', 'synthesis_cache_hits', 'model_requests', 'extraction_windows', 'extraction_workers', 'input_records', 'input_chars', 'cited_source_count',
    'archive_workers', 'archive_generated_batches', 'archive_cache_hits'] as const)
    if (count(raw[field])) result[field] = raw[field];
  if (typeof raw.default_generation_configuration === 'boolean') result.default_generation_configuration = raw.default_generation_configuration;
  if (Array.isArray(raw.batch_timings)) result.batch_timings = raw.batch_timings.flatMap((value) =>
    object(value) && count(value.batch) && value.batch > 0
      && typeof value.seconds === 'number' && Number.isFinite(value.seconds) && value.seconds >= 0
      && typeof value.cached === 'boolean'
      ? [{ batch: value.batch, seconds: value.seconds, cached: value.cached }] : []);
  if (object(raw.source_selection)) {
    const selection: NonNullable<GenerationDiagnostics['source_selection']> = {};
    for (const field of ['available_primary_fragments', 'selected_primary_fragments', 'context_only_fragments',
      'omitted_primary_fragments', 'selected_source_chars'] as const)
      if (count(raw.source_selection[field])) selection[field] = raw.source_selection[field];
    if (typeof raw.source_selection.partial === 'boolean') selection.partial = raw.source_selection.partial;
    result.source_selection = selection;
  }
  if (object(raw.overview_selection)) {
    const selection: NonNullable<GenerationDiagnostics['overview_selection']> = {};
    for (const field of ['available_items', 'selected_items', 'omitted_items', 'synthesis_requests', 'synthesis_cache_hits',
      'synthesis_batches_total', 'synthesis_batches_completed', 'synthesis_levels', 'full_input_chars', 'input_chars'] as const)
      if (count(raw.overview_selection[field])) selection[field] = raw.overview_selection[field];
    for (const field of ['partial', 'staged_synthesis'] as const)
      if (typeof raw.overview_selection[field] === 'boolean') selection[field] = raw.overview_selection[field];
    if (count(raw.overview_selection.synthesis_workers) && raw.overview_selection.synthesis_workers > 0)
      selection.synthesis_workers = raw.overview_selection.synthesis_workers;
    if (Array.isArray(raw.overview_selection.synthesis_batch_timings)) {
      selection.synthesis_batch_timings = raw.overview_selection.synthesis_batch_timings
        .slice(0, MAX_SYNTHESIS_TIMINGS).flatMap(synthesisTiming);
      if (raw.overview_selection.synthesis_batch_timings.length > MAX_SYNTHESIS_TIMINGS
          || raw.overview_selection.synthesis_batch_timings_truncated === true)
        selection.synthesis_batch_timings_truncated = true;
    }
    result.overview_selection = selection;
  }
  if (object(raw.profile)) {
    const profile: NonNullable<GenerationDiagnostics['profile']> = { notes: [] };
    for (const field of ['id', 'label'] as const) if (text(raw.profile[field])) profile[field] = raw.profile[field];
    if (count(raw.profile.window_chars)) profile.window_chars = raw.profile.window_chars;
    if (raw.profile.source_budget_chars === null || count(raw.profile.source_budget_chars))
      profile.source_budget_chars = raw.profile.source_budget_chars;
    for (const field of ['requested_effort', 'effective_effort'] as const)
      if (raw.profile[field] === null || text(raw.profile[field])) profile[field] = raw.profile[field];
    if (typeof raw.profile.experimental === 'boolean') profile.experimental = raw.profile.experimental;
    if (Array.isArray(raw.profile.notes)) profile.notes = raw.profile.notes.filter(text);
    result.profile = profile;
  }
  if (object(raw.token_usage)) {
    const usage = raw.token_usage;
    const number = (key: string) => count(usage[key]) ? usage[key] as number : null;
    const parsed: NonNullable<GenerationDiagnostics['token_usage']> = {
      total_tokens: number('total_tokens'), input_tokens: number('input_tokens'), output_tokens: number('output_tokens'),
      cached_input_tokens: number('cached_input_tokens'), reasoning_output_tokens: number('reasoning_output_tokens'),
      reported_requests: number('reported_requests'), unreported_requests: number('unreported_requests'),
      status: 'unavailable',
    };
    const measurements = [parsed.total_tokens, parsed.input_tokens, parsed.output_tokens,
      parsed.cached_input_tokens, parsed.reasoning_output_tokens];
    const available = measurements.some((value) => value !== null);
    if (available && usage.status !== 'unavailable') {
      parsed.status = usage.status === 'complete' && measurements.every((value) => value !== null)
        && parsed.reported_requests !== null && parsed.reported_requests > 0 && parsed.unreported_requests === 0
        && (result.model_requests === undefined || parsed.reported_requests === result.model_requests)
        ? 'complete' : 'partial';
    } else if (usage.status === 'unavailable') {
      parsed.total_tokens = parsed.input_tokens = parsed.output_tokens = null;
      parsed.cached_input_tokens = parsed.reasoning_output_tokens = null;
    }
    result.token_usage = parsed;
  }
  return result;
}

export function diagnosticCount(value: number | null | undefined) {
  return value === null || value === undefined ? 'Unavailable' : value.toLocaleString();
}
