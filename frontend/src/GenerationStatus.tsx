import { useEffect, useState } from 'react';
import { api } from './api';

export type GenerationProvenance = { account: string; model: string; effort: string };
type Progress = {
  state: 'idle' | 'running' | 'completed' | 'failed';
  stage: string;
  sessions: number;
  turns: number;
  batches: number;
  error?: string;
};

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
  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const query = new URLSearchParams({ kind });
        const status = await api<Progress>(`${base}/codex/generation/status?${query}`, { signal: abort.signal });
        if (abort.signal.aborted) return;
        setProgress(status);
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
  const counts = progress && [
    [progress.sessions, 'sessions'],
    [progress.turns, 'turns'],
    [progress.batches, 'batches'],
  ].filter(([count]) => Number(count) > 0)
    .map(([count, name]) => `${Number(count).toLocaleString()} ${name}`);
  return (
    <div className="generation-progress" role="status" aria-live="polite" aria-atomic="true">
      {error ? (
        <p>{label && `${label}: `}Generation is still waiting for its result. Progress unavailable: {error}</p>
      ) : (
        <p>
          {label && `${label}: `}
          {progress?.stage || 'Starting generation…'}
          {!!counts?.length && ` · ${counts.join(' · ')}`}
          {progress?.state === 'failed' && progress.error && ` · ${progress.error}`}
        </p>
      )}
    </div>
  );
}
