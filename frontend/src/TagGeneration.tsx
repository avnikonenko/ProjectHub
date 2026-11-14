import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { GenerationStatus, isGenerationCancelled, cancelledGenerationMessage } from './GenerationStatus';
import { Feedback } from './Feedback';
import { ErrorBox } from './ui';
import { parseTopicGroups, type TopicGroup } from './topicGroups';

export type TagResult = {
  target: 'knowledge_preview' | 'knowledge' | 'qa';
  publication_id?: string; generated_at?: string;
  topic_groups: TopicGroup[]; topic_grouping: Record<string, unknown>;
};

type Props = {
  base: string; target: TagResult['target']; publicationId?: string; generatedAt?: string;
  itemIds: string[]; hasTags: boolean; disabled?: boolean;
  generation: { model: string; effort: string };
  onResult: (result: TagResult) => void; onRecovered: () => void;
  onBusyChange?: (busy: boolean) => void;
};

// Opening a result only reads progress. Codex is contacted only by the explicit button.
export function TagGeneration(props: Props) {
  const { base, target, publicationId, generatedAt, itemIds, hasTags, disabled, generation } = props;
  const [busy, setBusy] = useState(false);
  const [localBusy, setLocalBusy] = useState(false);
  const [error, setError] = useState('');
  const [cancelled, setCancelled] = useState(false);
  const callbacks = useRef(props);
  callbacks.current = props;
  const request = useRef<AbortController | null>(null);
  const identity = JSON.stringify([base, target, publicationId, generatedAt]);
  useEffect(() => {
    callbacks.current.onBusyChange?.(busy || localBusy);
  }, [busy, localBusy]);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let sawRunning = false;
    async function poll() {
      try {
        const status = await api<{ state: string; error?: string }>(`${base}/codex/generation/status?kind=tags`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        const running = status.state === 'running';
        setBusy(running);
        if (running) sawRunning = true;
        if (sawRunning && !running) {
          sawRunning = false;
          callbacks.current.onRecovered();
          if (status.state === 'failed') setError(status.error || 'AI tag generation failed. Saved tags are preserved.');
          if (status.state === 'cancelled') setCancelled(true);
        }
      } catch (e) {
        if (controller.signal.aborted) return;
        setError(`Could not recover AI tag progress: ${(e as Error).message}`);
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 1500);
    }
    void poll();
    return () => {
      controller.abort(); request.current?.abort(); request.current = null;
      if (timer) clearTimeout(timer);
      callbacks.current.onBusyChange?.(false);
    };
  }, [base, identity]);
  async function generate() {
    if (disabled || busy || localBusy || request.current || !itemIds.length) return;
    const controller = new AbortController(); request.current = controller;
    setLocalBusy(true); setError(''); setCancelled(false);
    try {
      const result = await api<TagResult>(`${base}/codex/tags`, { method: 'POST', signal: controller.signal,
        body: JSON.stringify({ confirmed: true, target, publication_id: publicationId, generated_at: generatedAt,
          model: generation.model || null, effort: generation.effort || null }) });
      if (controller.signal.aborted) return;
      if (result.target !== target || (target === 'qa' ? result.generated_at !== generatedAt : result.publication_id !== publicationId)
          || !Array.isArray(result.topic_groups) || !result.topic_grouping || typeof result.topic_grouping !== 'object' || Array.isArray(result.topic_grouping))
        throw new Error('AI tags do not match the selected saved version. Reload before retrying.');
      callbacks.current.onResult({ ...result, topic_groups: parseTopicGroups(result.topic_groups, itemIds) });
    } catch (e) {
      if (!controller.signal.aborted) {
        if (isGenerationCancelled(e)) setCancelled(true);
        else setError((e as Error).message);
      }
    } finally {
      if (request.current === controller) { request.current = null; setLocalBusy(false); }
    }
  }
  return <div className="tag-generation">
    <button className="secondary" type="button" disabled={disabled || busy || localBusy || !itemIds.length}
      onClick={() => void generate()}>{localBusy ? 'Generating AI tags…' : 'Generate AI tags'}</button>
    <small className="muted"> Sends this saved version’s item statements or questions and answers to OpenAI through Codex to organize AI tags.</small>
    <ErrorBox error={error} />
    {cancelled && <Feedback>{cancelledGenerationMessage}</Feedback>}
    {(busy || localBusy) && <GenerationStatus base={base} kind="tags" label="AI tag generation" />}
  </div>;
}
