import { useEffect, useState, type ReactNode } from 'react';
import { api } from './api';
import { ErrorBox } from './ui';
import { projectGenerationKinds, schemeFocusLabels, type GenerationRetryView, type ProjectGenerationKind, type SchemeFocus } from './generationBatch';
import { generationProfileLabels, isGenerationProfileId, type GenerationProfileId } from './generationDiagnostics';
import { generationDestination, generationProviderLabels, type GenerationProvider } from './generationProvider';
import './generationResults.css';

export type GenerationResultState = 'saved' | 'partial' | 'not_generated' | 'running' | 'waiting' | 'failed'
  | 'timed_out' | 'cancelled' | 'interrupted' | 'skipped' | 'earlier_result';
const numericalSettingLabels = {
  generation_budget: 'Overall time budget',
  generation_token_limit: 'Token limit',
  extraction_workers: 'AI workers per job',
  scheme_max_nodes: 'Scheme node limit',
  scheme_source_chars: 'Scheme excerpt budget',
  scheme_read_bytes: 'Local file read limit',
  scheme_package_chars: 'Final evidence package limit',
  max_items: 'Maximum questions',
} as const;
type NumericalSetting = keyof typeof numericalSettingLabels;
type AttemptSettings = {
  account: string;
  model: string;
  effort: string;
  provider?: GenerationProvider;
  generation_profile?: GenerationProfileId;
  scheme_focus?: SchemeFocus;
  project_instructions?: boolean;
} & Partial<Record<NumericalSetting, number>>;
type AttemptProgress = {
  stage?: string;
  batch_phase?: string;
  batch_completed?: number;
  batch_total?: number;
  cache_hits?: number;
  elapsed_seconds?: number;
  model_requests?: number;
};
const numericalSettings = Object.keys(numericalSettingLabels) as NumericalSetting[];
export type GenerationResultView = GenerationRetryView & {
  state: GenerationResultState;
  history_at?: string | null;
  showing_previous: boolean;
  latest_attempt: {
    state: string;
    started_at?: string | null;
    finished_at?: string | null;
    error?: string | null;
    saves_result?: boolean;
    settings?: AttemptSettings | null;
    progress?: AttemptProgress | null;
  } | null;
};
export type GenerationResultsData = { project_id: number; views: GenerationResultView[] };
export type GenerationResultsState = { base: string; data: GenerationResultsData | null; loading: boolean; error: string };
export type GenerationSelection = { project_id: number; selected: ProjectGenerationKind[] };
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown, max = 10000): value is string => typeof value === 'string' && value.length <= max;
const nullableText = (value: unknown) => value === undefined || value === null || text(value);
const validAttemptProgress = (value: unknown) => value === undefined || value === null || (object(value)
  && [value.stage, value.batch_phase].every(part => part === undefined || text(part, 160))
  && [value.batch_completed, value.batch_total, value.cache_hits, value.model_requests].every(part =>
    part === undefined || (typeof part === 'number' && Number.isSafeInteger(part) && part >= 0))
  && (value.elapsed_seconds === undefined || (typeof value.elapsed_seconds === 'number'
    && Number.isFinite(value.elapsed_seconds) && value.elapsed_seconds >= 0)));
const validAttemptSettings = (value: unknown) => value === undefined || value === null
  || (object(value) && [value.account, value.model, value.effort].every(part => text(part) && part.trim())
    && (value.generation_profile === undefined || isGenerationProfileId(value.generation_profile))
    && (value.project_instructions === undefined || typeof value.project_instructions === 'boolean')
    && (value.provider === undefined || (typeof value.provider === 'string'
      && Object.hasOwn(generationProviderLabels, value.provider)))
    && (value.scheme_focus === undefined || (typeof value.scheme_focus === 'string'
      && Object.hasOwn(schemeFocusLabels, value.scheme_focus)))
    && numericalSettings.every(key => {
      const part = value[key];
      return part === undefined || (typeof part === 'number' && Number.isSafeInteger(part)
        && part >= (key === 'generation_token_limit' ? 0 : 1));
    }));
export const generationKindLabels: Record<ProjectGenerationKind, string> = {
  brief: 'Overview', knowledge: 'Knowledge', scheme: 'Scheme', qa: 'Q&A', parameters: 'Implementation Choices & Limits',
};
const states: Record<GenerationResultState, string> = {
  saved: 'Saved', partial: 'Partial result saved', not_generated: 'Not generated', running: 'Running', waiting: 'Waiting for Knowledge',
  failed: 'Failed', timed_out: 'Timed out', cancelled: 'Cancelled', interrupted: 'Interrupted', skipped: 'Skipped',
  earlier_result: 'Earlier result available',
};

export function initializeGenerationSelection(previous: GenerationSelection | null, data: GenerationResultsData): GenerationSelection {
  if (previous?.project_id === data.project_id) return previous;
  return { project_id: data.project_id, selected: data.views.filter(view => view.retryable
    && !['saved', 'running', 'waiting'].includes(view.state)).map(view => view.kind) };
}

export function selectableGenerationKinds(data: GenerationResultsData, busy: Partial<Record<ProjectGenerationKind, boolean>> = {}) {
  return data.views.filter(view => !busy[view.kind] && view.state !== 'running' && view.state !== 'waiting').map(view => view.kind);
}

export function parseGenerationResults(raw: unknown, projectId: number): GenerationResultsData {
  if (!object(raw) || raw.project_id !== projectId || !Array.isArray(raw.views)
      || raw.views.length !== projectGenerationKinds.length)
    throw new Error('Generation status is incomplete or belongs to another project.');
  const seen = new Set<string>();
  const views = raw.views.map(value => {
    if (!object(value) || !projectGenerationKinds.includes(value.kind as ProjectGenerationKind)
        || seen.has(String(value.kind)) || !Object.hasOwn(states, String(value.state))
        || !(value.saved_at === null || text(value.saved_at, 100))
        || typeof value.showing_previous !== 'boolean' || typeof value.retryable !== 'boolean')
      throw new Error('Generation status contains an invalid or duplicate view.');
    seen.add(String(value.kind));
    const historyAt = value.history_at ?? null;
    if ((historyAt !== null && (value.kind !== 'knowledge' || !text(historyAt, 100)
        || !historyAt.trim() || !Number.isFinite(Date.parse(historyAt))))
        || (value.state === 'earlier_result' && (value.kind !== 'knowledge' || historyAt === null
          || value.saved_at !== null || value.showing_previous)))
      throw new Error('The earlier generation result is invalid.');
    const attempt = value.latest_attempt;
    if (attempt !== null && (!object(attempt) || !text(attempt.state, 100)
        || (attempt.project_id !== undefined && attempt.project_id !== projectId)
        || (attempt.kind !== undefined && attempt.kind !== value.kind)
        || !nullableText(attempt.started_at) || !nullableText(attempt.finished_at) || !nullableText(attempt.error)
        || (attempt.saves_result !== undefined && typeof attempt.saves_result !== 'boolean')
        || !validAttemptSettings(attempt.settings) || !validAttemptProgress(attempt.progress)))
      throw new Error('The latest generation attempt is invalid.');
    return { kind: value.kind, state: value.state, saved_at: value.saved_at, history_at: historyAt,
      showing_previous: value.showing_previous, retryable: value.retryable, latest_attempt: attempt } as GenerationResultView;
  });
  return { project_id: projectId, views: projectGenerationKinds.map(kind => views.find(view => view.kind === kind)!) };
}

export async function readGenerationResults(base: string, signal: AbortSignal,
  request: (path: string, options: RequestInit) => Promise<unknown> = api): Promise<GenerationResultsData> {
  if (!/^\/projects\/[1-9]\d*$/.test(base) || !Number.isSafeInteger(Number(base.split('/')[2])))
    throw new Error('Generation status does not identify this project.');
  signal.throwIfAborted();
  const raw = await request(`${base}/codex/generation/summary`, { signal });
  signal.throwIfAborted();
  return parseGenerationResults(raw, Number(base.split('/')[2]));
}

function dateLabel(value: string | null | undefined) {
  if (!value || !Number.isFinite(Date.parse(value))) return null;
  return new Date(value).toLocaleString();
}

function attemptSettingValue(key: NumericalSetting, value: number) {
  switch (key) {
    case 'generation_budget':
      return `${value.toLocaleString()} seconds`;
    case 'generation_token_limit':
      return value ? `${value.toLocaleString()} reported tokens` : 'No limit';
    case 'scheme_max_nodes':
      return `${value.toLocaleString()} ${value === 1 ? 'node' : 'nodes'} per diagram`;
    case 'scheme_source_chars':
    case 'scheme_package_chars':
      return `${value.toLocaleString()} characters`;
    case 'scheme_read_bytes':
      return `${(value / (1024 * 1024)).toLocaleString()} MiB`;
    default:
      return value.toLocaleString();
  }
}

function GenerationAttemptSettings({ settings, title }: { settings: AttemptSettings; title: string }) {
  return <details>
    <summary>{title}</summary>
    <dl className="generation-attempt-settings">
      {settings.provider && <div><dt>Provider</dt><dd>{generationProviderLabels[settings.provider]}</dd></div>}
      <div><dt>Model</dt><dd>{settings.model}</dd></div>
      <div><dt>Reasoning effort</dt><dd>{settings.effort}</dd></div>
      {settings.generation_profile && <div>
        <dt>AI generation profile</dt><dd>{generationProfileLabels[settings.generation_profile]}</dd>
      </div>}
      {settings.project_instructions !== undefined && <div>
        <dt>Project AI instructions</dt><dd>{settings.project_instructions ? 'Custom' : 'Default'}</dd>
      </div>}
      {settings.scheme_focus && <div><dt>Scheme focus</dt><dd>{schemeFocusLabels[settings.scheme_focus]}</dd></div>}
      {numericalSettings.map(key => {
        const value = settings[key];
        if (value === undefined) return null;
        return <div key={key}><dt>{numericalSettingLabels[key]}</dt>
          <dd title={key === 'scheme_read_bytes' ? `${value.toLocaleString()} bytes` : undefined}>
            {attemptSettingValue(key, value)}
          </dd>
        </div>;
      })}
    </dl>
  </details>;
}

export function GenerationResultRows({ data, selected = [], onToggle, busy = {}, disabled = false, schemeFocus,
  onSchemeFocusChange, schemeFocusDisabled = false }: {
  data: GenerationResultsData; selected?: readonly ProjectGenerationKind[];
  onToggle?: (kind: ProjectGenerationKind, checked: boolean) => void;
  busy?: Partial<Record<ProjectGenerationKind, boolean>>; disabled?: boolean;
  schemeFocus?: SchemeFocus; onSchemeFocusChange?: (value: SchemeFocus) => void; schemeFocusDisabled?: boolean;
}) {
  return <ul className="generation-results-list">{data.views.map(view => <li key={view.kind}>
    <label className="generation-result-choice"><input type="checkbox" checked={selected.includes(view.kind)}
      aria-label={`Select ${generationKindLabels[view.kind]} for regeneration`}
      disabled={disabled || !onToggle || !!busy[view.kind] || view.state === 'running' || view.state === 'waiting'}
      onChange={event => onToggle?.(view.kind, event.target.checked)} /><span>{generationKindLabels[view.kind]}</span></label>
    <strong>{states[view.state]}</strong>
    <div className="generation-result-description">
      {view.kind === 'scheme' && schemeFocus && <label>Scheme focus for next run
        <select value={schemeFocus} onChange={event => onSchemeFocusChange?.(event.target.value as SchemeFocus)}
          disabled={disabled || schemeFocusDisabled || !onSchemeFocusChange || !!busy.scheme || view.state === 'running' || view.state === 'waiting'}>
          {(Object.keys(schemeFocusLabels) as SchemeFocus[]).map(option =>
            <option key={option} value={option}>{schemeFocusLabels[option]}</option>)}
        </select>
      </label>}
      {view.showing_previous ? <small>Showing the earlier saved result{dateLabel(view.saved_at) ? ` · ${dateLabel(view.saved_at)}` : ''}.</small>
        : view.saved_at ? <small>Saved {dateLabel(view.saved_at) ?? 'result available'}.</small>
          : view.history_at ? <small>Earlier saved version · {dateLabel(view.history_at)}.</small>
          : view.state !== 'saved' && <small>No saved result yet.</small>}
      {view.state === 'partial' && <small>Use Retry unfinished to try the incomplete steps again.</small>}
      {view.latest_attempt?.settings && <GenerationAttemptSettings settings={view.latest_attempt.settings}
        title={view.state === 'waiting' || view.state === 'skipped' ? 'Requested settings'
          : view.showing_previous || view.latest_attempt.saves_result === false ? 'Latest attempt settings' : 'Settings'} />}
      {view.latest_attempt && view.state !== 'saved' && view.state !== 'running' && view.state !== 'waiting' &&
        <details><summary>Attempt details</summary>
          {dateLabel(view.latest_attempt.started_at) && <p>Started {dateLabel(view.latest_attempt.started_at)}</p>}
          {dateLabel(view.latest_attempt.finished_at) && <p>Finished {dateLabel(view.latest_attempt.finished_at)}</p>}
          {view.latest_attempt.error && <p>{view.latest_attempt.error}</p>}
          {view.latest_attempt.progress?.stage && <p>Last stage · {view.latest_attempt.progress.stage}</p>}
          {view.latest_attempt.progress?.batch_completed !== undefined && <p>
            {view.latest_attempt.progress.batch_phase && <>{view.latest_attempt.progress.batch_phase} · </>}
            {view.latest_attempt.progress.batch_completed}
            {view.latest_attempt.progress.batch_total !== undefined && <> of {view.latest_attempt.progress.batch_total}</>}
            {' batches completed'}
            {view.latest_attempt.progress.cache_hits !== undefined && <> · {view.latest_attempt.progress.cache_hits} cache hits</>}
          </p>}
        </details>}
    </div>
  </li>)}</ul>;
}

/** Reads only. Polling stops once no view is running or waiting. */
export function GenerationResults({ base, refreshVersion, onState, selected = [], onSelectionChange, busy = {},
  onRegenerateSelected, regenerateDisabled = false, bulkActions, actionUsage, schemeFocus, onSchemeFocusChange, schemeFocusDisabled, provider }: {
  base: string; refreshVersion: number; onState: (state: GenerationResultsState) => void;
  selected?: readonly ProjectGenerationKind[]; onSelectionChange?: (selected: ProjectGenerationKind[]) => void;
  busy?: Partial<Record<ProjectGenerationKind, boolean>>;
  onRegenerateSelected?: () => void; regenerateDisabled?: boolean;
  bulkActions?: ReactNode;
  actionUsage?: ReactNode;
  schemeFocus?: SchemeFocus; onSchemeFocusChange?: (value: SchemeFocus) => void; schemeFocusDisabled?: boolean;
  provider?: GenerationProvider;
}) {
  const [state, setState] = useState<GenerationResultsState>({ base, data: null, loading: true, error: '' });
  const [reload, setReload] = useState(0);
  const current = state.base === base ? state : { base, data: null, loading: true, error: '' };
  useEffect(() => { onState(current); }, [state, base, onState]);
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    setState(previous => ({ base, data: previous.base === base ? previous.data : null,
      loading: true, error: '' }));
    const read = async () => {
      try {
        const data = await readGenerationResults(base, controller.signal);
        if (controller.signal.aborted) return;
        setState({ base, data, loading: false, error: '' });
        if (data.views.some(view => view.state === 'running' || view.state === 'waiting'))
          timer = setTimeout(read, 1500);
      } catch (error) {
        if (!controller.signal.aborted) setState(previous => ({ base, data: previous.base === base ? previous.data : null,
          loading: false, error: (error as Error).message }));
      }
    };
    void read();
    return () => { controller.abort(); if (timer !== undefined) clearTimeout(timer); };
  }, [base, refreshVersion, reload]);
  return <section className="generation-results" aria-label="Generation results" aria-busy={current.loading}>
    <h2>Generation results</h2>
    <small className="muted">Generation sends selected project evidence to {generationDestination(provider)}.</small>
    <div className="generation-selection-controls">
      {current.data && <button className="secondary" onClick={onRegenerateSelected}
        disabled={regenerateDisabled || current.loading || !!current.error || !onRegenerateSelected}>Regenerate selected</button>}
      {bulkActions}
    </div>
    {actionUsage}
    {current.loading && !current.data && <p className="muted">Loading generation status…</p>}
    {current.error && <><ErrorBox error={`Could not load generation status: ${current.error}`} />
      <button className="quiet" onClick={() => setReload(value => value + 1)}>Reload status</button></>}
    {current.data && <>
      <div className="generation-selection-controls">
        <button className="quiet" disabled={current.loading || !!current.error || !onSelectionChange}
          onClick={() => onSelectionChange?.(selectableGenerationKinds(current.data!, busy))}>Select all</button>
        <button className="quiet" disabled={!selected.length || !onSelectionChange}
          onClick={() => onSelectionChange?.([])}>Clear selection</button>
      </div>
      <small className="muted">Scheme uses saved Knowledge unless Knowledge is also selected.</small>
      <GenerationResultRows data={current.data} selected={selected} busy={busy} disabled={current.loading || !!current.error}
        schemeFocus={schemeFocus} onSchemeFocusChange={onSchemeFocusChange} schemeFocusDisabled={schemeFocusDisabled}
        onToggle={(kind, checked) => onSelectionChange?.(checked ? [...new Set([...selected, kind])]
          : selected.filter(value => value !== kind))} />
    </>}
    <details className="generation-results-help">
      <summary>Regenerate all details</summary>
      <p>Overview, Q&amp;A and Implementation Choices &amp; Limits run in parallel. Scheme starts once Knowledge passes validation and saves. If Knowledge fails or is cancelled, Scheme is skipped. Tags are generated separately.</p>
    </details>
  </section>;
}
