/** Saved input accounting, never an estimate of answer accuracy. */
export type RepositoryCoverage = {
  status: 'available' | 'unavailable' | 'not_linked' | 'not_used' | 'unrecorded';
  inventory_scope?: 'git_tracked'; inventory_complete?: boolean;
  total_files?: number | null; eligible_files?: number | null;
  read_files?: number; full_files?: number; partial_files?: number;
  excluded_files?: number; unsupported_files?: number; not_selected_files?: number;
  failed_files?: number; empty_files?: number; ai_input_files?: number;
  paths?: Partial<Record<'read' | 'partial' | 'not_selected' | 'failed' | 'empty', string[]>>;
  by_category?: Partial<Record<'docs' | 'manifest' | 'code' | 'tests', {
    eligible_files?: number; read_files?: number; full_files?: number; partial_files?: number;
  }>>;
  paths_truncated?: boolean; limits?: Record<string, number>; notes: string[];
};
export type ConversationCoverage = {
  inventory_scope: 'saved_source_manifest'; known_sources?: number; processed_sources?: number;
  partial_sources?: number; excluded_sources?: number; unavailable_sources?: number;
  available_fragments?: number; selected_fragments?: number; omitted_fragments?: number;
  notes: string[];
};
export type EvidenceCoverage = { repository?: RepositoryCoverage; conversations?: ConversationCoverage };
export type ArchiveConversations = {
  scope: 'linked_codex_sessions'; linked_sessions?: number; read_sessions?: number;
  finished_turns?: number; active_turns_skipped?: number; duplicate_turns_omitted?: number;
  unique_turns?: number; fragments?: number; notes: string[];
};
const object = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const strings = (value: unknown) => Array.isArray(value)
  ? value.filter((entry): entry is string => typeof entry === 'string' && entry.length <= 2000).slice(0, 100) : [];
const counts = (raw: Record<string, unknown>, keys: readonly string[]) =>
  Object.fromEntries(keys.filter(key => count(raw[key])).map(key => [key, raw[key] as number]));

export function parseEvidenceCoverage(raw: unknown): EvidenceCoverage | undefined {
  if (!object(raw)) return undefined;
  const result: EvidenceCoverage = {};
  const repo = raw.repository;
  if (object(repo) && ['available', 'unavailable', 'not_linked', 'not_used', 'unrecorded'].includes(String(repo.status))) {
    const value: RepositoryCoverage = { status: repo.status as RepositoryCoverage['status'], notes: strings(repo.notes),
      ...counts(repo, ['read_files', 'full_files', 'partial_files', 'excluded_files', 'unsupported_files',
        'not_selected_files', 'failed_files', 'empty_files', 'ai_input_files']) };
    for (const key of ['total_files', 'eligible_files'] as const)
      if (repo[key] === null || count(repo[key])) value[key] = repo[key];
    if (repo.inventory_scope === 'git_tracked') value.inventory_scope = 'git_tracked';
    if (typeof repo.inventory_complete === 'boolean') value.inventory_complete = repo.inventory_complete;
    if (typeof repo.paths_truncated === 'boolean') value.paths_truncated = repo.paths_truncated;
    const paths = repo.paths;
    if (object(paths)) value.paths = Object.fromEntries(['read', 'partial', 'not_selected', 'failed', 'empty']
      .filter(key => Array.isArray(paths[key])).map(key => [key, strings(paths[key])]));
    if (object(paths) && Object.values(paths).some(paths => Array.isArray(paths) && paths.length > 100))
      value.paths_truncated = true;
    const categories = repo.by_category;
    if (object(categories)) value.by_category = Object.fromEntries(['docs', 'manifest', 'code', 'tests'].flatMap(key => {
      const category = categories[key];
      if (!object(category)) return [];
      const parsed = counts(category, ['eligible_files', 'read_files', 'full_files', 'partial_files']);
      if ((count(parsed.eligible_files) && count(parsed.read_files) && parsed.read_files > parsed.eligible_files)
        || (count(parsed.read_files) && [parsed.full_files, parsed.partial_files].some(number => count(number) && number > parsed.read_files))
        || (count(parsed.read_files) && count(parsed.full_files) && count(parsed.partial_files)
          && parsed.full_files + parsed.partial_files !== parsed.read_files)) return [];
      return [[key, parsed]];
    }));
    if (object(repo.limits)) value.limits = Object.fromEntries(Object.entries(repo.limits)
      .filter(([key, entry]) => key.length <= 100 && count(entry)).slice(0, 20)) as Record<string, number>;
    const total = value.total_files, eligible = value.eligible_files, read = value.read_files;
    const bounded = [eligible, read, value.ai_input_files, value.excluded_files, value.unsupported_files,
      value.not_selected_files, value.failed_files, value.empty_files].every(number => number === undefined || number === null
      || !count(total) || number <= total);
    const consistent = bounded && !(count(eligible) && count(read) && read > eligible)
      && !(count(read) && count(value.ai_input_files) && value.ai_input_files > read)
      && !(count(read) && [value.full_files, value.partial_files].some(number => count(number) && number > read))
      && !(count(eligible) && [value.not_selected_files, value.failed_files, value.empty_files]
        .some(number => count(number) && number > eligible))
      && !(count(total) && count(eligible) && count(value.excluded_files) && count(value.unsupported_files)
        && eligible + value.excluded_files + value.unsupported_files !== total)
      && !(count(eligible) && count(read) && count(value.not_selected_files) && count(value.failed_files) && count(value.empty_files)
        && read + value.not_selected_files + value.failed_files + value.empty_files !== eligible)
      && !(count(read) && count(value.full_files) && count(value.partial_files)
        && value.full_files + value.partial_files !== read);
    if (consistent) result.repository = value;
  }
  const conversation = raw.conversations;
  if (object(conversation) && conversation.inventory_scope === 'saved_source_manifest') {
    const value: ConversationCoverage = { inventory_scope: 'saved_source_manifest', notes: strings(conversation.notes),
      ...counts(conversation, ['known_sources', 'processed_sources', 'partial_sources', 'excluded_sources',
        'unavailable_sources', 'available_fragments', 'selected_fragments', 'omitted_fragments']) };
    if (!(count(value.known_sources) && [value.processed_sources, value.partial_sources,
      value.excluded_sources, value.unavailable_sources].some(number => count(number) && number > value.known_sources!))
      && !(count(value.available_fragments) && count(value.selected_fragments)
        && value.selected_fragments > value.available_fragments)) result.conversations = value;
  }
  return Object.keys(result).length ? result : undefined;
}

export function parseArchiveConversations(raw: unknown): ArchiveConversations | undefined {
  if (!object(raw) || raw.scope !== 'linked_codex_sessions') return undefined;
  const value: ArchiveConversations = { scope: 'linked_codex_sessions', notes: strings(raw.notes),
    ...counts(raw, ['linked_sessions', 'read_sessions', 'finished_turns', 'active_turns_skipped',
      'duplicate_turns_omitted', 'unique_turns', 'fragments']) };
  if ((count(value.linked_sessions) && count(value.read_sessions) && value.read_sessions > value.linked_sessions)
    || (count(value.finished_turns) && count(value.unique_turns) && value.unique_turns > value.finished_turns)
    || (count(value.finished_turns) && count(value.unique_turns) && count(value.duplicate_turns_omitted)
      && value.unique_turns + value.duplicate_turns_omitted !== value.finished_turns)) return undefined;
  return value;
}
