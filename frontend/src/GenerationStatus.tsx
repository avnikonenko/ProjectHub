import { Feedback } from './Feedback';
import { ErrorBox } from './ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, post } from './api';
import { generationProviderLabels, type GenerationProvider } from './generationProvider';

type SavedGenerationKind = 'brief' | 'qa' | 'scheme' | 'parameters';
type RecoveryKind = SavedGenerationKind | 'knowledge';
export type GenerationKind = SavedGenerationKind | 'ask' | 'knowledge' | 'tags';
const recoveryLabels: Record<RecoveryKind, string> = {
  brief: 'Project Overview', knowledge: 'Knowledge Summary', scheme: 'Scheme',
  qa: 'AI Q&A', parameters: 'Implementation Choices & Limits',
};

export type GenerationProvenance = { account: string; model: string; effort: string; provider?: GenerationProvider };
type ProgressSettings = { provider?: GenerationProvider };
export type Progress = {
  state: 'idle' | 'running' | 'completed' | 'failed' | 'cancelled';
  stage: string;
  sessions: number;
  error?: string;
  started_at?: string;
  cancel_requested?: boolean;
  batch_total?: number;
  batch_completed?: number;
  batch_started_at?: string | null;
  last_batch_seconds?: number | null;
  batch_phase?: string;
  cache_hits?: number;
  budget_seconds?: number;
  elapsed_seconds?: number;
  settings?: ProgressSettings | null;
  latest_attempt?: { state: string; started_at?: string; settings?: ProgressSettings | null } | null;
};

// The status API preserves its legacy idle state for queued dependencies.
// Their durable waiting state still needs polling and duplicate protection.
const pendingGeneration = (status: Progress) => status.state === 'running'
  || status.latest_attempt?.state === 'waiting';

type RecoveryState = {
  checking: boolean; running: boolean; version: number; error: string;
  errorKind?: 'attempt' | 'status'; progress?: Progress;
};
const initialRecovery: RecoveryState = { checking: true, running: false, version: 0, error: '' };

function useKindRecovery(base: string, kind: RecoveryKind, enabled: boolean) {
  const [request, setRequest] = useState<{ previousStartedAt?: string } | null>(null);
  const [state, setState] = useState(initialRecovery);

  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let handoffChecks = 0;

    async function poll(recovering = false) {
      try {
        const status = await api<Progress>(`${base}/codex/generation/status?kind=${kind}`, { signal: abort.signal });
        if (abort.signal.aborted) return;
        const running = pendingGeneration(status);
        // A lost POST response can precede the server starting its run.
        const beforeReplacement = request !== null && status.started_at === request.previousStartedAt;
        if (!recovering && beforeReplacement && !running && handoffChecks < 2) {
          handoffChecks++;
          timer = setTimeout(() => void poll(), 1500);
          return;
        }
        // Failed attempts found on mount or still present during a retry
        // belong in the view's saved attempt details.
        const failedCurrentAttempt = status.state === 'failed'
          && (recovering || (request !== null && !beforeReplacement));
        const error = failedCurrentAttempt
          ? status.error || 'AI generation failed. The previous result is preserved.'
          : status.state === 'idle' && recovering && !running
            ? 'Generation is no longer running. The server may have restarted; check the saved result before retrying.'
            : '';
        setState(previous => ({
          checking: false, running, progress: status, error, errorKind: error ? 'attempt' : undefined,
          version: previous.version + Number(!running && (recovering || request !== null)),
        }));
        if (running) timer = setTimeout(() => void poll(true), 1500);
      } catch (error) {
        if (abort.signal.aborted) return;
        setState(previous => ({ ...previous, checking: true, errorKind: 'status',
          error: `Could not recover AI progress: ${(error as Error).message}` }));
        timer = setTimeout(() => void poll(recovering), 1500);
      }
    }

    void poll();
    return () => { abort.abort(); if (timer !== undefined) clearTimeout(timer); };
  }, [base, kind, enabled, request]);

  const clearError = useCallback((attemptOnly = false) => {
    setState(previous => previous.error && (!attemptOnly || previous.errorKind === 'attempt')
      ? { ...previous, error: '', errorKind: undefined } : previous);
  }, []);

  return {
    ...state,
    checking: enabled && state.checking,
    clearError,
    recheck: () => {
      clearError(true);
      setState(previous => ({ ...previous, checking: true }));
      setRequest({ previousStartedAt: state.progress?.started_at });
    },
  };
}

export function useGenerationRecovery(base: string, { includeBrief = true, includeKnowledge = false, includeParameters = false }: {
  includeBrief?: boolean; includeKnowledge?: boolean; includeParameters?: boolean;
} = {}) {
  const brief = useKindRecovery(base, 'brief', includeBrief);
  const qa = useKindRecovery(base, 'qa', true);
  const scheme = useKindRecovery(base, 'scheme', true);
  const knowledge = useKindRecovery(base, 'knowledge', includeKnowledge);
  const parameters = useKindRecovery(base, 'parameters', includeParameters);
  const states = { brief, qa, scheme, knowledge, parameters };
  const clearBrief = brief.clearError, clearQA = qa.clearError, clearScheme = scheme.clearError;
  const clearKnowledge = knowledge.clearError, clearParameters = parameters.clearError;
  const clearError = useCallback((kind?: RecoveryKind) => {
    const clear = { brief: clearBrief, qa: clearQA, scheme: clearScheme, knowledge: clearKnowledge, parameters: clearParameters };
    if (kind) clear[kind]();
    else Object.values(clear).forEach(clearKind => clearKind());
  }, [clearBrief, clearQA, clearScheme, clearKnowledge, clearParameters]);
  const checkingByKind = { brief: brief.checking, qa: qa.checking, scheme: scheme.checking,
    knowledge: knowledge.checking, parameters: parameters.checking };
  return {
    checking: Object.values(checkingByKind).some(Boolean), checkingByKind,
    running: { brief: brief.running, qa: qa.running, scheme: scheme.running, knowledge: knowledge.running, parameters: parameters.running },
    version: { brief: brief.version, qa: qa.version, scheme: scheme.version, knowledge: knowledge.version, parameters: parameters.version },
    progress: { brief: brief.progress, qa: qa.progress, scheme: scheme.progress,
      knowledge: knowledge.progress, parameters: parameters.progress },
    error: (Object.keys(states) as RecoveryKind[]).flatMap(kind => states[kind].error
      ? [`${recoveryLabels[kind]} · ${states[kind].error}`] : []).join(' '),
    clearError,
    clearAttemptErrors: (kinds: readonly RecoveryKind[]) => kinds.forEach(kind => states[kind].clearError(true)),
    recheck: (kind: RecoveryKind) => states[kind].recheck(),
  };
}

function describeProgress(stage: string, kind: GenerationKind, provider: string) {
  const session = /^Reading session (\d+) of (\d+)$/.exec(stage);
  if (session) return `Reading conversation ${session[1]} of ${session[2]}`;
  const batch = /^Generating Q&A batch (\d+) of (\d+)$/.exec(stage);
  if (batch) return `Waiting for ${provider} to write questions and answers (group ${batch[1]} of ${batch[2]})`;
  switch (stage) {
    case 'Reading project sessions': return 'Finding linked conversations';
    case 'Summarizing history batch': return 'Summarizing conversations';
    case 'Summarizing overview batch': return 'Summarizing part of the overview';
    case 'Summarizing overview batches': return 'Summarizing overview sections in parallel';
    case 'Combining overview batches': return 'Combining overview sections';
    case 'Reusing cached evidence batch': return 'Reusing a previously saved summary';
    case 'Combining evidence': return `Waiting for ${provider} to combine the evidence`;
    case 'Summarizing project notes and decisions': return 'Gathering project notes and decisions';
    case 'Collecting Scheme evidence': return 'Inspecting selected code and saved discussions';
    case 'Generating Scheme': return `Waiting for ${provider} to explain architecture and workflow`;
    case 'Validating Scheme': return 'Checking diagrams and source references';
    case 'Generating project brief': return `Waiting for ${provider} to write the project summary`;
    case 'Completed': return 'Finished. Loading the result…';
    case 'Cancelling': return `Cancelling… Waiting for ${provider} to stop.`;
    case 'Cancelled': return 'Cancelled. Saved results are unchanged.';
    case 'Failed': return 'Generation failed';
    case '':
    case 'Starting generation': return kind === 'tags' ? 'Preparing AI tags' : kind === 'scheme' ? 'Preparing project diagrams' : kind === 'brief' ? 'Gathering project information' : kind === 'parameters' ? 'Preparing implementation choices and limits' : kind === 'ask' ? 'Preparing an answer' : kind === 'knowledge' ? 'Preparing source-linked knowledge' : 'Preparing questions and answers';
    default: return stage;
  }
}

export function isGenerationCancelled(error: unknown) {
  return error instanceof Error && /^(?:AI )?generation cancelled\./i.test(error.message);
}

export const cancelledGenerationMessage = 'Cancelled. Saved results are unchanged.';

export function SavedGeneration({ generation }: { generation?: GenerationProvenance | null }) {
  const account = generation?.account?.trim() || 'Account not recorded';
  const model = generation?.model?.trim() || 'Model not recorded';
  const effort = generation?.effort?.trim() || 'Effort not recorded';
  const provider = generation?.provider && generationProviderLabels[generation.provider];
  const attribution = provider ? `Provider: ${provider}` : `Account: ${account}`;
  return <small className="generation-provenance" aria-label="Saved generation settings"
    title={`${attribution}; model: ${model}; reasoning effort: ${effort}`}>
    {provider || account} · {model} · {effort}
  </small>;
}

function durationLabel(seconds: number) {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

export function GenerationStatus({
  base, kind, label, onRunningChange, sharedProgress, onProgressUpdate,
}: {
  base: string;
  kind: GenerationKind;
  label?: string;
  onRunningChange?: (running: boolean) => void;
  sharedProgress?: Progress | null;
  onProgressUpdate?: (progress: Progress) => void;
}) {
  const [localProgress, setProgress] = useState<Progress | null>(null);
  const externallyManaged = sharedProgress !== undefined;
  const progress = externallyManaged ? sharedProgress : localProgress;
  const [error, setError] = useState('');
  const [cancelError, setCancelError] = useState('');
  const [cancellingFor, setCancellingFor] = useState<string | null>(null);
  const cancelRequest = useRef<AbortController | null>(null);
  const [startedAt, setStartedAt] = useState(Date.now);
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    onRunningChange?.(progress?.state === 'running');
  }, [progress?.state, onRunningChange]);
  useEffect(() => {
    if (progress?.state !== 'running') return;
    const timer = setInterval(() => setElapsed(Math.max(0, Math.floor((Date.now() - startedAt) / 1000))), 1000);
    return () => clearInterval(timer);
  }, [startedAt, progress?.state]);
  useEffect(() => {
    if (!externallyManaged) return;
    if (sharedProgress?.state === 'running') {
      const started = Date.parse(sharedProgress.started_at ?? '');
      if (Number.isFinite(started)) {
        setStartedAt(started);
        setElapsed(Math.max(0, Math.floor((Date.now() - started) / 1000)));
      }
    }
    if (sharedProgress?.cancel_requested || sharedProgress?.state === 'cancelled') setCancelError('');
  }, [externallyManaged, sharedProgress]);
  useEffect(() => () => { cancelRequest.current?.abort(); cancelRequest.current = null; }, [base, kind]);
  useEffect(() => {
    if (externallyManaged) return;
    const abort = new AbortController();
    setProgress(null);
    setCancelError('');
    setCancellingFor(null);
    let timer: ReturnType<typeof setTimeout> | undefined;
    let sawRunning = false;
    async function poll() {
      try {
        const query = new URLSearchParams({ kind });
        const status = await api<Progress>(`${base}/codex/generation/status?${query}`, { signal: abort.signal });
        if (abort.signal.aborted) return;
        // A fast first poll can return the previous request's terminal state.
        if (status.state === 'running') {
          sawRunning = true;
          const started = Date.parse(status.started_at ?? '');
          if (Number.isFinite(started)) {
            setStartedAt(started);
            setElapsed(Math.max(0, Math.floor((Date.now() - started) / 1000)));
          }
        }
        if (sawRunning || kind === 'ask' || status.latest_attempt?.state === 'waiting') setProgress(status);
        if (status.cancel_requested || status.state === 'cancelled') setCancelError('');
        setError('');
      } catch (e) {
        if (abort.signal.aborted) return;
        setError((e as Error).message);
      }
      if (!abort.signal.aborted) timer = setTimeout(poll, 1500);
    }
    void poll();
    return () => {
      abort.abort();
      cancelRequest.current?.abort();
      cancelRequest.current = null;
      if (timer) clearTimeout(timer);
    };
  }, [base, kind, externallyManaged]);
  async function cancel() {
    if (progress?.state !== 'running' || !progress.started_at || cancelRequest.current
        || progress.cancel_requested || cancellingFor === progress.started_at) return;
    const started = progress.started_at;
    const controller = new AbortController();
    cancelRequest.current = controller;
    setCancellingFor(started);
    setCancelError('');
    try {
      // Tab changes must not abort a cancellation already requested by the user.
      // The controller guards local state updates; the server request continues.
      const result = await post(`${base}/codex/generation/cancel`, { kind, started_at: started }) as Progress;
      if (!controller.signal.aborted) { setProgress(result); onProgressUpdate?.(result); }
    } catch (e) {
      if (!controller.signal.aborted) {
        setCancellingFor(null);
        setCancelError(`Could not confirm cancellation: ${(e as Error).message}`);
      }
    } finally {
      if (cancelRequest.current === controller) cancelRequest.current = null;
    }
  }
  const cancelling = progress?.state === 'running'
    && (progress.cancel_requested || cancellingFor === progress.started_at);
  const attempt = progress?.latest_attempt;
  const settings = progress?.settings ?? (attempt?.started_at === progress?.started_at ? attempt?.settings : null);
  const provider = settings ? generationProviderLabels[settings.provider ?? 'codex'] ?? 'AI' : 'AI';
  const measuredElapsed = progress?.state === 'running' ? elapsed : progress?.elapsed_seconds ?? elapsed;
  const elapsedLabel = durationLabel(measuredElapsed);
  const batchStarted = Date.parse(progress?.batch_started_at ?? '');
  const batchElapsed = Number.isFinite(batchStarted) ? Math.max(0, (Date.now() - batchStarted) / 1000) : null;
  const batchPhase = progress?.batch_phase === 'AI tagging' ? 'AI tagging'
    : progress?.batch_phase === 'history' ? 'Conversation summaries'
    : progress?.batch_phase === 'conversation_overviews_v1' ? 'Conversation overviews'
    : progress?.batch_phase === 'reduction' ? 'Evidence reduction'
      : progress?.batch_phase === 'knowledge extraction' ? 'Knowledge extraction'
        : progress?.batch_phase === 'overview synthesis' ? 'Overview synthesis' : 'Batches';
  const hasBatchTotal = Number.isFinite(progress?.batch_total) && (progress?.batch_total ?? 0) > 0;
  const budget = progress?.budget_seconds;
  const hasBudget = typeof budget === 'number' && Number.isFinite(budget) && budget > 0;
  if (kind === 'ask' && (!progress || progress.state === 'idle' || progress.state === 'completed')) return null;
  return (
    <>
    <Feedback className="generation-progress" tone={progress?.state === 'failed' ? 'error' : progress?.state === 'completed' ? 'success' : 'info'}>
      <p>
        <strong>{label ?? 'AI generation'}</strong> · <span aria-live="off">Elapsed {elapsedLabel}</span>
      </p>
      <div>
        <p>{error ? 'Progress updates are temporarily unavailable. Still waiting for the result.'
          : describeProgress(cancelling ? 'Cancelling' : progress?.stage ?? '', kind, provider)}</p>
        {!error && progress?.state === 'failed' && progress.error && <p>{progress.error}</p>}
        {!error && hasBatchTotal && (
          <p>{batchPhase}: {progress?.batch_completed ?? 0} of {progress?.batch_total} batches completed.
            {' '}This count covers this phase only.</p>
        )}
        {!error && progress?.state === 'running' && batchElapsed !== null && (
          <p>Current batch: {durationLabel(batchElapsed)}</p>
        )}
        {!error && typeof progress?.last_batch_seconds === 'number' && (
          <p>Previous batch: {durationLabel(progress.last_batch_seconds)}
            {typeof progress.cache_hits === 'number' ? ` · ${progress.cache_hits} cached batches reused` : ''}</p>
        )}
        {hasBudget && (
          <p>Overall time budget: {durationLabel(budget)}
            {progress?.state === 'running' ? ` · ${durationLabel(budget - measuredElapsed)} remaining` : ''}</p>
        )}
        {!error && progress?.state === 'running' && progress.sessions > 0 && (
          <p>{progress.sessions.toLocaleString()} {progress.sessions === 1 ? 'conversation processed' : 'conversations processed'}</p>
        )}
      </div>
      {progress?.state === 'running' && progress.started_at && (
        <button type="button" className="secondary" disabled={!!cancelling}
          onClick={() => void cancel()}>
          {cancelling ? 'Cancelling…' : 'Cancel generation'}
        </button>
      )}
    </Feedback>
    <ErrorBox error={cancelError} />
    </>
  );
}
