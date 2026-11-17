import { useEffect, useRef } from 'react';

export const projectGenerationKinds = ['brief', 'knowledge', 'scheme', 'qa', 'parameters'] as const;
export type ProjectGenerationKind = typeof projectGenerationKinds[number];
export type GenerationRequestState = 'wait' | 'ready' | 'skip';
export type GenerationRequestTracker = { identity: string; sequence: number };
export type GenerationRequestProps = {
  generationRequest?: number;
  onGenerationRequestHandled?: (started: boolean) => void;
};
export type KnowledgeGenerationOutcome = {
  request: number;
  state: 'saved' | 'failed' | 'cancelled';
  publicationId?: string;
  schemeRequested?: boolean;
};
export type KnowledgeSchemeDependency = { project: string; request: number };
export type GenerationPlan = {
  start: ProjectGenerationKind[];
  waitForKnowledge: boolean;
  schemeSkippedForKnowledge: boolean;
};
export type GenerationRetryView = {
  kind: ProjectGenerationKind;
  state: string;
  saved_at: string | null;
  retryable: boolean;
  showing_previous?: boolean;
};
const unfinishedGenerationStates = new Set([
  'not_generated', 'earlier_result', 'failed', 'timed_out', 'cancelled', 'interrupted', 'skipped',
]);

/** Only unfinished views are retried. A successful view is never refreshed here. */
export function planRetryProjectGeneration(views: readonly GenerationRetryView[],
  busy: Partial<Record<ProjectGenerationKind, boolean>>): GenerationPlan {
  const byKind = new Map(views.map(view => [view.kind, view]));
  const start = projectGenerationKinds.filter(kind => {
    const view = byKind.get(kind);
    return !!view?.retryable && unfinishedGenerationStates.has(view.state) && !busy[kind];
  });
  if (!start.includes('scheme')) return { start, waitForKnowledge: false, schemeSkippedForKnowledge: false };
  const knowledge = byKind.get('knowledge');
  if (busy.knowledge || knowledge?.state === 'running' || knowledge?.state === 'waiting')
    return { start: start.filter(kind => kind !== 'scheme'), waitForKnowledge: false, schemeSkippedForKnowledge: true };
  if (start.includes('knowledge'))
    return { start: start.filter(kind => kind !== 'scheme'), waitForKnowledge: true, schemeSkippedForKnowledge: false };
  if (knowledge?.state !== 'saved')
    return { start: start.filter(kind => kind !== 'scheme'), waitForKnowledge: false, schemeSkippedForKnowledge: true };
  return { start, waitForKnowledge: false, schemeSkippedForKnowledge: false };
}

/** Explicit selection may refresh saved views, but never starts a busy sibling. */
export function planSelectedProjectGeneration(selected: readonly ProjectGenerationKind[],
  views: readonly GenerationRetryView[], busy: Partial<Record<ProjectGenerationKind, boolean>>): GenerationPlan {
  const requested = new Set(selected);
  const byKind = new Map(views.map(view => [view.kind, view]));
  const start = projectGenerationKinds.filter(kind => requested.has(kind) && !busy[kind]
    && !!byKind.get(kind) && !['running', 'waiting'].includes(byKind.get(kind)!.state));
  if (!start.includes('scheme')) return { start, waitForKnowledge: false, schemeSkippedForKnowledge: false };
  const knowledge = byKind.get('knowledge');
  if (busy.knowledge || knowledge?.state === 'running' || knowledge?.state === 'waiting')
    return { start: start.filter(kind => kind !== 'scheme'), waitForKnowledge: false, schemeSkippedForKnowledge: true };
  if (start.includes('knowledge'))
    return { start: start.filter(kind => kind !== 'scheme'), waitForKnowledge: true, schemeSkippedForKnowledge: false };
  return { start, waitForKnowledge: false, schemeSkippedForKnowledge: false };
}

/** Scheme must never use older Knowledge just because its generation is idle. */
export function planProjectGeneration(busy: Partial<Record<ProjectGenerationKind, boolean>>): GenerationPlan {
  const idle = idleGenerationKinds(busy);
  return {
    start: idle.filter(kind => kind !== 'scheme'),
    waitForKnowledge: !busy.scheme && !busy.knowledge,
    schemeSkippedForKnowledge: !busy.scheme && !!busy.knowledge,
  };
}

export function resolveKnowledgeSchemeDependency(pending: KnowledgeSchemeDependency | null,
  project: string, outcome: KnowledgeGenerationOutcome): 'start' | 'skip' | 'ignore' {
  if (!pending || pending.project !== project || pending.request !== outcome.request) return 'ignore';
  return outcome.state === 'saved' && typeof outcome.publicationId === 'string'
    && !!outcome.publicationId.trim() && outcome.schemeRequested === true ? 'start' : 'skip';
}

/** Claim a UI request before invoking the existing view-specific generation action. */
export function claimGenerationRequest(tracker: GenerationRequestTracker, identity: string,
  sequence: number, state: GenerationRequestState): 'start' | 'skip' | null {
  if (tracker.identity !== identity) { tracker.identity = identity; tracker.sequence = 0; }
  if (!Number.isSafeInteger(sequence) || sequence <= tracker.sequence || sequence <= 0 || state === 'wait') return null;
  tracker.sequence = sequence;
  return state === 'ready' ? 'start' : 'skip';
}

export function useGenerationRequest(identity: string, sequence: number, state: GenerationRequestState,
  generate: () => Promise<void>, handled?: (started: boolean) => void) {
  const tracker = useRef<GenerationRequestTracker>({ identity, sequence: 0 });
  const current = useRef({ generate, handled }); current.current = { generate, handled };
  useEffect(() => {
    const action = claimGenerationRequest(tracker.current, identity, sequence, state);
    if (action === null) return;
    // The normal handler remains the only POST/save/recovery path.
    if (action === 'start') void current.current.generate();
    current.current.handled?.(action === 'start');
  }, [identity, sequence, state]);
}

export function idleGenerationKinds(busy: Partial<Record<ProjectGenerationKind, boolean>>) {
  return projectGenerationKinds.filter(kind => !busy[kind]);
}
