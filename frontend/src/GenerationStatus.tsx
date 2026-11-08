import { useEffect, useState } from 'react';
import { api } from './api';

export type GenerationProvenance = { account: string; model: string; effort: string };
type Progress = {
  state: 'idle' | 'running' | 'completed' | 'failed';
  stage: string;
  sessions: number;
  error?: string;
};

function describeProgress(stage: string, kind: 'brief' | 'qa') {
  const session = /^Reading session (\d+) of (\d+)$/.exec(stage);
  if (session) return `Reading conversation ${session[1]} of ${session[2]}`;
  const batch = /^Generating Q&A batch (\d+) of (\d+)$/.exec(stage);
  if (batch) return `Waiting for Codex to write questions and answers (group ${batch[1]} of ${batch[2]})`;
  switch (stage) {
    case 'Reading project sessions': return 'Finding linked conversations';
    case 'Summarizing history batch': return 'Waiting for Codex to summarize part of the conversation history';
    case 'Reusing cached evidence batch': return 'Reusing a previously saved summary';
    case 'Combining evidence': return 'Waiting for Codex to combine the evidence';
    case 'Summarizing project notes and decisions': return 'Gathering project notes and decisions';
    case 'Generating project brief': return 'Waiting for Codex to write the project summary';
    case 'Completed': return 'Finished. Loading the result…';
    case 'Failed': return 'Generation failed';
    case '':
    case 'Starting generation': return kind === 'brief' ? 'Gathering project information' : 'Preparing questions and answers';
    default: return stage;
  }
}

export function SavedGeneration({ generation }: { generation?: GenerationProvenance | null }) {
  const account = generation?.account?.trim() || 'Account not recorded';
  const model = generation?.model?.trim() || 'Model not recorded';
  const effort = generation?.effort?.trim() || 'Effort not recorded';
  return <small className="generation-provenance" aria-label="Saved generation settings"
    title={`Account: ${account}; model: ${model}; reasoning effort: ${effort}`}>
    {account} · {model} · {effort}
  </small>;
}

export function GenerationStatus({
  base, kind, label,
}: {
  base: string;
  kind: 'brief' | 'qa';
  label?: string;
}) {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState('');
  const [startedAt] = useState(Date.now);
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [startedAt]);
  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let sawRunning = false;
    async function poll() {
      try {
        const query = new URLSearchParams({ kind });
        const status = await api<Progress>(`${base}/codex/generation/status?${query}`, { signal: abort.signal });
        if (abort.signal.aborted) return;
        // A fast first poll can return the previous request's terminal state.
        if (status.state === 'running') sawRunning = true;
        if (sawRunning) setProgress(status);
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
      if (timer) clearTimeout(timer);
    };
  }, [base, kind]);
  const elapsedLabel = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, '0')}`;
  return (
    <div className="generation-progress">
      <p>
        <strong>{label ?? 'AI generation'}</strong> · <span>Elapsed {elapsedLabel}</span>
      </p>
      <div role="status" aria-atomic="true">
        <p>{error ? 'Progress updates are temporarily unavailable. Still waiting for the result.'
          : describeProgress(progress?.stage ?? '', kind)}</p>
        {!error && progress?.state === 'failed' && progress.error && <p>{progress.error}</p>}
        {!error && progress?.state === 'running' && progress.sessions > 0 && (
          <p>{progress.sessions.toLocaleString()} {progress.sessions === 1 ? 'conversation processed' : 'conversations processed'}</p>
        )}
      </div>
    </div>
  );
}
