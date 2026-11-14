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
export type GenerationDiagnostics = {
  duration_seconds?: number; ai_requests?: number; cache_hits?: number;
  synthesis_requests?: number; model_requests?: number;
  extraction_windows?: number; extraction_workers?: number; default_generation_configuration?: boolean;
  batch_timings?: { batch: number; seconds: number; cached: boolean }[];
  source_selection?: {
    available_primary_fragments?: number; selected_primary_fragments?: number;
    context_only_fragments?: number; omitted_primary_fragments?: number;
    selected_source_chars?: number; partial?: boolean;
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

/** Diagnostics never invalidate an otherwise valid legacy preview or invent unavailable measurements. */
export function parseGenerationDiagnostics(raw: unknown): GenerationDiagnostics | undefined {
  if (!object(raw)) return undefined;
  const result: GenerationDiagnostics = {};
  if (typeof raw.duration_seconds === 'number' && Number.isFinite(raw.duration_seconds) && raw.duration_seconds >= 0)
    result.duration_seconds = raw.duration_seconds;
  for (const field of ['ai_requests', 'cache_hits', 'synthesis_requests', 'model_requests', 'extraction_windows', 'extraction_workers'] as const)
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
