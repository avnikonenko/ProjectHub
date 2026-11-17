import type { BriefSource } from './BriefReferences';
import type { GenerationProvenance } from './GenerationStatus';

export type ParameterBasis = 'documented' | 'inferred' | 'unknown';
export type ParameterSource = BriefSource & {
  provenance?: string; content_hash?: string; path?: string; revision?: string;
  line_start?: number; line_end?: number; symbol?: string;
};
export type ProjectParameter = {
  id: string; name: string; component: string; path: string; symbol: string;
  line_start: number; line_end: number;
  default_value: string | null; default_kind: 'literal' | 'expression' | 'required' | 'unspecified';
  declaration_kind?: string;
  condition?: string;
  source_ids: string[];
  purpose: string; purpose_basis: ParameterBasis;
  rationale: string; rationale_basis: 'documented' | 'unknown';
  trade_off: string; trade_off_basis: ParameterBasis;
  units: string; explanation_source_ids: string[];
};
export type ParameterSnapshot = {
  project_id?: number; generated_at: string; items: ProjectParameter[]; sources: ParameterSource[];
  fingerprint: string; coverage: Record<string, unknown>; generation?: GenerationProvenance | null;
  diagnostics?: Record<string, unknown> | null;
};
export type ParameterResult = {
  snapshot: ParameterSnapshot | null;
  freshness: { state: string; partial: boolean; limitations: string[] };
  inspection?: { coverage?: Record<string, unknown>; diagnostics?: Record<string, unknown> };
};

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const string = (value: unknown, max = 2000): value is string => typeof value === 'string' && value.length <= max;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(entry => string(entry));
const positive = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0;
const bases = ['documented', 'inferred', 'unknown'];

export function parseParameterSnapshot(raw: unknown, projectId: number): ParameterSnapshot {
  if (!object(raw) || (raw.project_id !== undefined && raw.project_id !== projectId)
      || !string(raw.generated_at, 100) || !raw.generated_at.trim()
      || !string(raw.fingerprint, 256) || !object(raw.coverage)
      || !Array.isArray(raw.items) || raw.items.length > 1000
      || !Array.isArray(raw.sources) || raw.sources.length > 4000)
    throw new Error('The saved parameter result is incomplete or belongs to another project.');
  const sourceIds = new Set<string>();
  const sources: ParameterSource[] = [];
  for (const source of raw.sources) {
    if (!object(source) || !string(source.id, 240) || !source.id || sourceIds.has(source.id)
        || !string(source.label, 500) || !string(source.text, 100_000)
        || (source.project_id !== undefined && source.project_id !== projectId)
        || ['path', 'revision', 'symbol', 'provenance', 'content_hash'].some(key =>
          source[key] !== undefined && !string(source[key], 2000))
        || (source.line_start !== undefined && !positive(source.line_start))
        || (source.line_end !== undefined && (!positive(source.line_end)
          || (positive(source.line_start) && source.line_end < source.line_start))))
      throw new Error('The saved parameter evidence is invalid.');
    sourceIds.add(source.id);
    sources.push(Object.fromEntries(Object.entries(source).filter(([key]) =>
      ['id', 'label', 'text', 'path', 'revision', 'symbol', 'provenance', 'content_hash', 'line_start', 'line_end'].includes(key))) as ParameterSource);
  }
  const generation = raw.generation;
  if (generation !== undefined && generation !== null && (!object(generation)
      || ['account', 'model', 'effort'].some(key => !string(generation[key], 1000))))
    throw new Error('The saved parameter generation metadata is invalid.');
  const ids = new Set<string>();
  const items = raw.items.map(value => {
    if (!object(value) || !string(value.id, 240) || !value.id || ids.has(value.id)
        || (value.project_id !== undefined && value.project_id !== projectId)
        || ['name', 'component', 'path', 'symbol'].some(key => !string(value[key], 1000))
        || !value.name || !value.path || !positive(value.line_start) || !positive(value.line_end)
        || value.line_end < value.line_start
        || !['literal', 'expression', 'required', 'unspecified'].includes(String(value.default_kind))
        || (value.declaration_kind !== undefined && !string(value.declaration_kind, 120))
        || (value.condition !== undefined && !string(value.condition, 2000))
        || (value.default_kind === 'literal' || value.default_kind === 'expression'
          ? !string(value.default_value, 4000) : value.default_value !== null)
        || !strings(value.source_ids) || !value.source_ids.length
        || value.source_ids.some(id => !sourceIds.has(id)))
      throw new Error('The saved parameter declarations are invalid.');
    ids.add(value.id);
    for (const key of ['purpose', 'rationale', 'trade_off', 'units'])
      if (value[key] !== undefined && !string(value[key], 4000))
        throw new Error('The saved parameter explanation is invalid.');
    if ((value.purpose_basis !== undefined && !bases.includes(String(value.purpose_basis)))
        || (value.trade_off_basis !== undefined && !bases.includes(String(value.trade_off_basis)))
        || (value.rationale_basis !== undefined && !['documented', 'unknown'].includes(String(value.rationale_basis)))
        || (value.explanation_source_ids !== undefined && (!strings(value.explanation_source_ids)
          || value.explanation_source_ids.some(id => !sourceIds.has(id)))))
      throw new Error('The saved parameter explanation evidence is invalid.');
    return { ...value, purpose: value.purpose ?? '', purpose_basis: value.purpose_basis ?? 'unknown',
      rationale: value.rationale ?? '', rationale_basis: value.rationale_basis ?? 'unknown',
      trade_off: value.trade_off ?? '', trade_off_basis: value.trade_off_basis ?? 'unknown',
      units: value.units ?? '', explanation_source_ids: value.explanation_source_ids ?? [] } as ProjectParameter;
  });
  return { ...raw, items, sources } as ParameterSnapshot;
}

export function parseParameterResult(raw: unknown, projectId: number): ParameterResult {
  if (!object(raw) || !('snapshot' in raw) || !object(raw.freshness)
      || !string(raw.freshness.state, 100) || typeof raw.freshness.partial !== 'boolean'
      || !strings(raw.freshness.limitations)) throw new Error('The parameter response is incomplete.');
  return { ...raw, snapshot: raw.snapshot === null ? null : parseParameterSnapshot(raw.snapshot, projectId) } as ParameterResult;
}

export function parameterDefault(item: ProjectParameter): string {
  if (item.default_kind === 'required') return 'Required — no declared default';
  if (item.default_kind === 'unspecified') return 'No declared default';
  return item.default_value === '' ? '"" (empty string)' : item.default_value ?? 'No declared default';
}

export function filterParameters(items: ProjectParameter[], query: string, component = '', sources: ParameterSource[] = []) {
  const normalize = (value: string) => value.toLocaleLowerCase().replace(/\s+/g, ' ').trim();
  const search = normalize(query);
  const indexed = new Map(sources.map(source => [source.id, source]));
  return items.filter(item => (!component || item.component === component)
    && (!search || normalize([item.name, item.component, item.path, item.symbol,
      item.default_value ?? '', item.condition ?? '', item.purpose, item.rationale, item.trade_off, item.units,
      ...[...item.source_ids, ...item.explanation_source_ids].flatMap(id => {
        const source = indexed.get(id); return source ? [source.label, source.text] : [];
      })].join(' ')).includes(search)));
}

export function parameterGroups(items: ProjectParameter[]): [string, ProjectParameter[]][] {
  const groups = new Map<string, ProjectParameter[]>();
  for (const item of items) groups.set(item.component, [...(groups.get(item.component) ?? []), item]);
  return [...groups].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
}
