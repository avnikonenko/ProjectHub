import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { ProjectGenerationKind } from './generationBatch';
import { observeGenerationAction, type GenerationActionStatus } from './generationAction';
import { generationKindLabels } from './GenerationResults';

export function GenerationActionUsage({ base, actionId, kinds, pending, onSettled }: {
  base: string;
  actionId: string;
  kinds?: readonly ProjectGenerationKind[];
  pending: boolean;
  onSettled: (actionId: string) => void;
}) {
  const [usage, setUsage] = useState<GenerationActionStatus | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [retry, setRetry] = useState(0);
  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  useEffect(() => {
    const controller = new AbortController();
    void observeGenerationAction(controller.signal, () => pendingRef.current,
      signal => api(`${base}/codex/generation/actions/${actionId}`, { signal, cache: 'no-store' }),
      status => {
        setUsage(status);
        setUnavailable(false);
      }).then(result => {
      if (controller.signal.aborted) return;
      setUnavailable(result === 'unavailable');
      if (result === 'finished') onSettled(actionId);
    });
    return () => controller.abort();
  }, [base, actionId, onSettled, retry]);
  const prefix = kinds?.length ? `${kinds.map(kind => generationKindLabels[kind]).join(' · ')} — ` : '';
  const retryButton = unavailable && <button type="button" className="quiet" onClick={() => {
    setUnavailable(false);
    setRetry(attempt => attempt + 1);
  }}>Retry token usage</button>;
  if (!usage) return <p className="muted">{prefix}{unavailable ? 'Token usage unavailable.' : 'Waiting for token usage…'}
    {' '}{retryButton}</p>;
  const reported = usage.total_tokens?.toLocaleString();
  return <p className="muted">
    {prefix}<strong>{reported === undefined ? 'Reported tokens unavailable'
      : `${reported}${usage.limit ? ` / ${usage.limit.toLocaleString()}` : ''} reported tokens`}</strong>
    {reported === undefined && usage.limit > 0 && <> · Stop at {usage.limit.toLocaleString()}</>}
    {usage.reported_requests < usage.model_requests && <> · {usage.reported_requests} of {usage.model_requests} requests reported</>}
    {usage.reached && <> · Limit reached</>}
    {unavailable && <> · Last available update {' '}{retryButton}</>}
  </p>;
}
