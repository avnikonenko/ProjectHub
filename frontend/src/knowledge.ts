import { parseTopicGroups, type TopicGroup } from './topicGroups.ts';
import { parseGenerationDiagnostics, type GenerationDiagnostics } from './generationDiagnostics.ts';
import { chatGPTTranscriptHref, historyTurnHref } from './historyLinks.ts';
import type { Brief } from './BriefContent';

export const statusOrder = ['AGREED', 'UNDER_EVALUATION', 'UNRESOLVED', 'PROPOSED', 'REJECTED', 'UNKNOWN'] as const;
export type KnowledgeEvidence = {
  source_id: string; role: string; excerpt: string; label?: string;
  conversation_id?: string; order?: number; timestamp?: string | null; revision?: string;
};
export type KnowledgeSource = {
  id: string; label?: string; text?: string; role?: string;
  session_id?: number; turn_id?: string; note_id?: number;
};
export type KnowledgeItem = {
  id: string; project_id?: number; statement: string; category: string;
  design_status: string; scope: string; rationale?: string; alternatives?: string;
  limitations?: string; evidence: KnowledgeEvidence[]; source_state?: string;
  supersedes?: string[]; superseded_by?: string[]; conflicts_with?: string[];
  reconciliation_state?: string; reconciliation_note?: string; agreement_basis?: string;
  relationships?: { kind?: string; target?: string; status?: string; source_ids?: string[] }[];
};
export type KnowledgeCoverage = Record<string, string[]>;
export type KnowledgeSnapshot = {
  project_id: number; publication_id?: string; generated_at?: string;
  items: KnowledgeItem[]; sources: KnowledgeSource[]; coverage?: KnowledgeCoverage;
  extraction_version?: string; source_manifest?: unknown;
  topic_groups?: TopicGroup[]; topic_grouping?: Record<string, unknown>;
};
export type KnowledgeResponse = {
  snapshot: KnowledgeSnapshot | null; paired_with_current_brief?: boolean;
  current_evidence_availability: Record<string, string>; coverage: KnowledgeCoverage;
};
export type KnowledgeLink = { projectId: number; itemId?: string; publicationId?: string };
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((entry) => typeof entry === 'string');
function coverage(value: unknown): KnowledgeCoverage {
  if (!object(value)) return {};
  return Object.fromEntries(Object.entries(value).filter(([, entries]) => strings(entries))) as KnowledgeCoverage;
}
export function parseKnowledgeResponse(raw: unknown, projectId: number): KnowledgeResponse {
  if (!object(raw) || !('snapshot' in raw)) throw new Error('Invalid project knowledge response.');
  let snapshot: KnowledgeSnapshot | null = null;
  if (raw.snapshot !== null) {
    const value = raw.snapshot;
    if (!object(value) || value.project_id !== projectId || !Array.isArray(value.items) || !Array.isArray(value.sources))
      throw new Error('Knowledge snapshot does not belong to this project or is malformed.');
    const ids = new Set<string>();
    for (const item of value.items) {
      if (!object(item) || !['id', 'statement', 'category', 'design_status', 'scope'].every((key) => typeof item[key] === 'string')
          || !item.id || ids.has(item.id as string) || (item.project_id !== undefined && item.project_id !== projectId)
          || !Array.isArray(item.evidence) || item.evidence.some((ref) => !object(ref)
            || !['source_id', 'role', 'excerpt'].every((key) => typeof ref[key] === 'string')
            || ['label', 'conversation_id', 'revision'].some((key) => ref[key] !== undefined && typeof ref[key] !== 'string')
            || (ref.timestamp !== undefined && ref.timestamp !== null && typeof ref.timestamp !== 'string')
            || (ref.order !== undefined && !Number.isSafeInteger(ref.order)))
          || ['rationale', 'alternatives', 'limitations', 'source_state', 'reconciliation_state', 'reconciliation_note', 'agreement_basis'].some((key) => item[key] !== undefined && typeof item[key] !== 'string')
          || ['conflicts_with', 'supersedes', 'superseded_by'].some((key) => item[key] !== undefined && !strings(item[key])))
        throw new Error('Invalid project knowledge item.');
      if (item.relationships !== undefined && (!Array.isArray(item.relationships) || item.relationships.some((relation) =>
        !object(relation) || ['kind', 'target', 'status'].some((key) => relation[key] !== undefined && typeof relation[key] !== 'string'))))
        throw new Error('Invalid knowledge relationships.');
      ids.add(item.id as string);
    }
    const sourceIds = new Set<string>();
    if (value.sources.some((source) => {
      if (!object(source) || typeof source.id !== 'string' || sourceIds.has(source.id)
        || (source.project_id !== undefined && source.project_id !== projectId)
        || ['label', 'text', 'role', 'turn_id'].some((key) => source[key] !== undefined && typeof source[key] !== 'string'))
        return true;
      sourceIds.add(source.id); return false;
    })
        || ['publication_id', 'generated_at', 'extraction_version'].some((key) => value[key] !== undefined && typeof value[key] !== 'string'))
      throw new Error('Invalid knowledge source metadata.');
    snapshot = { ...value, coverage: coverage(value.coverage),
      ...(value.topic_groups !== undefined ? { topic_groups: parseTopicGroups(value.topic_groups, ids) } : {}),
    } as KnowledgeSnapshot;
  }
  const availability = object(raw.current_evidence_availability)
    ? Object.fromEntries(Object.entries(raw.current_evidence_availability).filter(([, value]) => typeof value === 'string')) as Record<string, string> : {};
  return { snapshot, paired_with_current_brief: raw.paired_with_current_brief === true,
    current_evidence_availability: availability, coverage: coverage(raw.coverage) };
}
export function matchesPublication(snapshot: KnowledgeSnapshot | null | undefined, projectId: number, publicationId: string | null | undefined) {
  return !!snapshot && snapshot.project_id === projectId && typeof publicationId === 'string' && !!publicationId.trim()
    && snapshot.publication_id === publicationId;
}
export function knowledgeHref({ projectId, itemId, publicationId }: KnowledgeLink) {
  const query = new URLSearchParams({ project: String(projectId) });
  if (itemId) query.set('item', itemId);
  if (publicationId) query.set('publication', publicationId);
  return '#knowledge?' + query;
}
export function readKnowledgeLink(hash: string): KnowledgeLink | null {
  if (!hash.startsWith('#knowledge?')) return null;
  const query = new URLSearchParams(hash.slice('#knowledge?'.length));
  const projectId = Number(query.get('project'));
  if (!Number.isSafeInteger(projectId) || projectId < 1) return null;
  return { projectId, itemId: query.get('item') || undefined, publicationId: query.get('publication') || undefined };
}
export const normalizeKnowledgeText = (text: string) => text.toLocaleLowerCase().replace(/\s+/g, ' ').trim();
export function filterKnowledge(items: KnowledgeItem[], query = '', status = '', category = '', scope = '', sources: KnowledgeSource[] = []) {
  const needle = normalizeKnowledgeText(query);
  const titles = new Map(sources.map((source) => [source.id, source.label || '']));
  return items.filter((item) => (!status || item.design_status === status) && (!category || item.category === category)
    && (!scope || item.scope === scope) && (!needle || normalizeKnowledgeText([
      item.statement, item.rationale, item.alternatives, item.limitations, item.scope, item.category, item.design_status,
      ...item.evidence.flatMap((ref) => [ref.excerpt, ref.label, titles.get(ref.source_id)]),
    ].filter(Boolean).join(' ')).includes(needle)));
}
export function evidenceAvailability(item: KnowledgeItem, availability: Record<string, string> = {}) {
  return availability[item.id] || item.source_state || 'unknown';
}
export type KnowledgePassageGroup = { primary: KnowledgeItem; related: KnowledgeItem[] };
// Shared passage is a display relationship, never evidence of semantic equivalence.
// Only direct user choices qualify: a numbered acceptance can refer to multiple proposals.
export function knowledgePassageGroups(snapshot: KnowledgeSnapshot, items = snapshot.items,
  availability: Record<string, string> = {}): KnowledgePassageGroup[] {
  const linked = new Set(snapshot.items.flatMap(item => [
    ...(item.conflicts_with || []), ...(item.supersedes || []), ...(item.superseded_by || []),
    ...(item.relationships || []).flatMap(relation => relation.target ? [relation.target] : []),
  ]));
  const byPassage = new Map<string, KnowledgePassageGroup>();
  const groups: KnowledgePassageGroup[] = [];
  for (const item of [...items].sort((a, b) => a.id.localeCompare(b.id))) {
    const userRefs = item.evidence.filter(ref => ref.role === 'user');
    const eligible = (item.project_id === undefined || item.project_id === snapshot.project_id)
      && item.design_status === 'AGREED' && item.agreement_basis === 'direct_user'
      && evidenceAvailability(item, availability) === 'valid' && !linked.has(item.id)
      && !item.conflicts_with?.length && !item.supersedes?.length && !item.superseded_by?.length
      && !item.relationships?.length && item.reconciliation_state !== 'unresolved'
      && userRefs.length > 0 && userRefs.every(ref => ref.source_id.trim() && ref.excerpt.trim()
        && typeof ref.revision === 'string' && /^[a-f0-9]{64}$/i.test(ref.revision));
    const key = eligible ? JSON.stringify([snapshot.project_id, item.design_status, item.category, item.scope,
      [...new Set(userRefs.map(ref => JSON.stringify([ref.source_id, ref.revision, ref.excerpt])))].sort()]) : null;
    const existing = key ? byPassage.get(key) : undefined;
    if (existing) existing.related.push(item);
    else {
      const group = { primary: item, related: [] as KnowledgeItem[] };
      groups.push(group);
      if (key) byPassage.set(key, group);
    }
  }
  return groups;
}
export function visibleKnowledgeGroups(groups: KnowledgePassageGroup[], limit: number, selectedId?: string) {
  const visible = groups.slice(0, limit);
  const selected = groups.find(group => group.primary.id === selectedId || group.related.some(item => item.id === selectedId));
  if (selected && !visible.includes(selected)) visible.push(selected);
  return visible;
}
export function importantKnowledge(snapshot: KnowledgeSnapshot, availability: Record<string, string> = {}) {
  // Stable ID order is a display fallback, not a chronology or importance score.
  const conflictTargets = new Set(snapshot.items.flatMap((item) => item.conflicts_with || []));
  const candidates = snapshot.items.filter((item) => evidenceAvailability(item, availability) === 'valid'
    && !item.superseded_by?.length && !item.conflicts_with?.length && !conflictTargets.has(item.id)
    && item.reconciliation_state !== 'unresolved')
    .sort((a, b) => a.id.localeCompare(b.id));
  const seen = new Set<string>();
  const distinct = knowledgePassageGroups(snapshot, candidates, availability).map(group => group.primary).filter((item) => {
    const key = JSON.stringify([normalizeKnowledgeText(item.statement), item.scope, item.design_status]);
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
  const agreed = distinct.filter((item) => item.design_status === 'AGREED').slice(0, 5);
  return [...agreed, ...distinct.filter((item) => item.design_status === 'UNRESOLVED').slice(0, Math.min(2, 5 - agreed.length))];
}
export function knowledgeSourceHref(projectId: number, source: KnowledgeSource | undefined, availability: string) {
  if (!source || availability !== 'valid') return null;
  if (Number.isSafeInteger(source.session_id) && source.session_id! > 0 && typeof source.turn_id === 'string' && source.turn_id)
    return historyTurnHref({ projectId, sessionId: source.session_id!, turnId: source.turn_id });
  if (Number.isSafeInteger(source.note_id) && source.note_id! > 0)
    return chatGPTTranscriptHref({ projectId, noteId: source.note_id! });
  return null;
}

export function parseKnowledgePreview(raw: unknown, projectId: number): { knowledge: KnowledgeSnapshot; brief: Brief; diagnostics?: GenerationDiagnostics } {
  if (!raw || typeof raw !== 'object') throw new Error('The preview response is invalid.');
  const result = raw as Record<string, any>;
  const brief = result.brief;
  const parsed = parseKnowledgeResponse({ snapshot: result.knowledge }, projectId);
  if (result.published !== false || !parsed.snapshot || !brief || typeof brief !== 'object'
      || !matchesPublication(parsed.snapshot, projectId, brief.project_state?.publication_id)
      || result.publication_id !== parsed.snapshot.publication_id) {
    throw new Error('The preview brief and knowledge do not share this project publication.');
  }
  const sections = brief.sections;
  if (!sections || typeof sections !== 'object' || Array.isArray(sections)
      || !Array.isArray(sections.overview) || !Array.isArray(sections.current_status)
      || !Array.isArray(sections.architecture)
      || !Object.values(sections).every((entries) => Array.isArray(entries) && entries.every((entry) =>
        entry && typeof entry.text === 'string' && ['verified', 'reported', 'inferred', 'unresolved'].includes(entry.classification) && Array.isArray(entry.source_ids)
        && entry.source_ids.every((id: unknown) => typeof id === 'string')))
      || !Array.isArray(brief.sources) || !brief.sources.every((source: any) => source
        && typeof source.id === 'string' && typeof source.label === 'string' && typeof source.text === 'string'
        && (source.turn_id === undefined || typeof source.turn_id === 'string')
        && ['note_id', 'session_id'].every(key => source[key] === undefined || (Number.isSafeInteger(source[key]) && source[key] > 0))
        && (source.shared_sessions === undefined || (Array.isArray(source.shared_sessions) && source.shared_sessions.every((session: any) =>
          session && Number.isSafeInteger(session.session_id) && session.session_id > 0 && typeof session.turn_id === 'string'
          && (session.session_title === undefined || typeof session.session_title === 'string')))))
      || !Array.isArray(brief.coverage) || !brief.coverage.every((value: unknown) => typeof value === 'string')
      || typeof brief.generated_at !== 'string') {
    throw new Error('The preview brief is incomplete. Published knowledge has not changed.');
  }
  const diagnostics = parseGenerationDiagnostics(result.diagnostics);
  return { knowledge: parsed.snapshot, brief, ...(diagnostics === undefined ? {} : { diagnostics }) };
}

