import { useEffect, useState, type ReactNode } from 'react';
import { api } from './api';
import { ErrorBox } from './ui';
import { projectGenerationKinds, type GenerationRetryView, type ProjectGenerationKind } from './generationBatch';
import './generationResults.css';

export type GenerationResultState = 'saved' | 'not_generated' | 'running' | 'waiting' | 'failed'
  | 'timed_out' | 'cancelled' | 'interrupted' | 'skipped' | 'earlier_result';
export type GenerationResultView = GenerationRetryView & {
  state: GenerationResultState;
  history_at?: string | null;
  showing_previous: boolean;
  latest_attempt: { state: string; started_at?: string | null; finished_at?: string | null; error?: string | null; saves_result?: boolean } | null;
};
export type GenerationResultsData = { project_id: number; views: GenerationResultView[] };
export type GenerationResultsState = { base: string; data: GenerationResultsData | null; loading: boolean; error: string };
export type GenerationSelection = { project_id: number; selected: ProjectGenerationKind[] };
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown, max = 10000): value is string => typeof value === 'string' && value.length <= max;
const nullableText = (value: unknown) => value === undefined || value === null || text(value);
const labels: Record<ProjectGenerationKind, string> = {
  brief: 'Overview', knowledge: 'Knowledge', scheme: 'Scheme', qa: 'Q&A', parameters: 'Implementation Choices & Limits',
};
const states: Record<GenerationResultState, string> = {
  saved: 'Saved', not_generated: 'Not generated', running: 'Running', waiting: 'Waiting for Knowledge',
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
        || (attempt.saves_result !== undefined && typeof attempt.saves_result !== 'boolean')))
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

export function GenerationResultRows({ data, selected = [], onToggle, busy = {}, disabled = false }: {
  data: GenerationResultsData; selected?: readonly ProjectGenerationKind[];
  onToggle?: (kind: ProjectGenerationKind, checked: boolean) => void;
  busy?: Partial<Record<ProjectGenerationKind, boolean>>; disabled?: boolean;
}) {
  return <ul className="generation-results-list">{data.views.map(view => <li key={view.kind}>
    <label className="generation-result-choice"><input type="checkbox" checked={selected.includes(view.kind)}
      aria-label={`Select ${labels[view.kind]} for regeneration`}
      disabled={disabled || !onToggle || !!busy[view.kind] || view.state === 'running' || view.state === 'waiting'}
      onChange={event => onToggle?.(view.kind, event.target.checked)} /><span>{labels[view.kind]}</span></label>
    <strong>{states[view.state]}</strong>
    <div className="generation-result-description">
      {view.showing_previous ? <small>Showing the earlier saved result{dateLabel(view.saved_at) ? ` · ${dateLabel(view.saved_at)}` : ''}.</small>
        : view.saved_at ? <small>Saved {dateLabel(view.saved_at) ?? 'result available'}.</small>
          : view.history_at ? <small>Earlier saved version · {dateLabel(view.history_at)}.</small>
          : view.state !== 'saved' && <small>No saved result yet.</small>}
      {view.latest_attempt && view.state !== 'saved' && view.state !== 'running' && view.state !== 'waiting' &&
        <details><summary>Attempt details</summary>
          {dateLabel(view.latest_attempt.started_at) && <p>Started {dateLabel(view.latest_attempt.started_at)}</p>}
          {dateLabel(view.latest_attempt.finished_at) && <p>Finished {dateLabel(view.latest_attempt.finished_at)}</p>}
          {view.latest_attempt.error && <p>{view.latest_attempt.error}</p>}
        </details>}
    </div>
  </li>)}</ul>;
}

/** Reads only. Polling stops once no view is running or waiting. */
export function GenerationResults({ base, refreshVersion, onState, selected = [], onSelectionChange, busy = {},
  onRegenerateSelected, regenerateDisabled = false, bulkActions }: {
  base: string; refreshVersion: number; onState: (state: GenerationResultsState) => void;
  selected?: readonly ProjectGenerationKind[]; onSelectionChange?: (selected: ProjectGenerationKind[]) => void;
  busy?: Partial<Record<ProjectGenerationKind, boolean>>;
  onRegenerateSelected?: () => void; regenerateDisabled?: boolean;
  bulkActions?: ReactNode;
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
    <small className="muted">Generation sends selected project evidence to OpenAI through Codex.</small>
    <div className="generation-selection-controls">
      {current.data && <button className="secondary" onClick={onRegenerateSelected}
        disabled={regenerateDisabled || current.loading || !!current.error || !onRegenerateSelected}>Regenerate selected</button>}
      {bulkActions}
    </div>
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
        onToggle={(kind, checked) => onSelectionChange?.(checked ? [...new Set([...selected, kind])]
          : selected.filter(value => value !== kind))} />
    </>}
    <details className="generation-results-help">
      <summary>Regenerate all details</summary>
      <p>Overview, Q&amp;A and Implementation Choices &amp; Limits run in parallel. Scheme starts once Knowledge passes validation and saves. If Knowledge fails or is cancelled, Scheme is skipped. Tags are generated separately.</p>
    </details>
  </section>;
}
