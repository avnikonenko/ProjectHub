import { SettingsHelp } from './SettingsHelp';
import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { GenerationStatus, isGenerationCancelled, cancelledGenerationMessage } from './GenerationStatus';
import { Feedback } from './Feedback';
import { ErrorBox } from './ui';
import { parseTopicGroups, type TopicGroup } from './topicGroups';
import { tagProgress, type TagProgressState } from './tagProgress';

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
  onBusyChange?: (busy: boolean) => void; visible?: boolean;
};

// Opening a result only reads progress. Codex is contacted only by the explicit button.
export function TagGeneration(props: Props) {
  const { base, target, publicationId, generatedAt, itemIds, hasTags, disabled, generation, visible = true } = props;
  const [busy, setBusy] = useState(false);
  const [localBusy, setLocalBusy] = useState(false);
  const [error, setError] = useState('');
  const [progressError, setProgressError] = useState('');
  const [cancelled, setCancelled] = useState(false);
  const [shared, setShared] = useState<TagProgressState>({ progress: null, error: '', starting: false });
  const callbacks = useRef(props);
  callbacks.current = props;
  const request = useRef<AbortController | null>(null);
  const identity = JSON.stringify([base, target, publicationId, generatedAt]);
  useEffect(() => {
    callbacks.current.onBusyChange?.(busy || localBusy);
  }, [busy, localBusy]);
  const terminalSeen = useRef('');
  const sawRunning = useRef(false);
  useEffect(() => {
    terminalSeen.current = '';
    sawRunning.current = false;
    setBusy(false);
    setLocalBusy(false);
    setError(''); setProgressError(''); setCancelled(false);
    setShared({ progress: null, error: '', starting: false });
    return () => {
      request.current?.abort(); request.current = null;
      callbacks.current.onBusyChange?.(false);
    };
  }, [identity]);
  useEffect(() => {
    if (!visible && !busy && !localBusy) return;
    return tagProgress.subscribe(base, state => {
      setShared(state);
      setProgressError(state.error ? `Could not recover AI tag progress: ${state.error}` : '');
      const status = state.progress;
      const running = status?.state === 'running';
      setBusy(running || state.starting);
      if (running) sawRunning.current = true;
      if (status && status.state !== 'running' && status.state !== 'idle' && !state.starting) {
        const terminal = JSON.stringify([status.started_at, status.state]);
        if (terminalSeen.current !== terminal) {
          terminalSeen.current = terminal;
          callbacks.current.onRecovered();
          if (sawRunning.current && status.state === 'failed') setError(status.error || 'AI tag generation failed. Saved tags are preserved.');
          if (sawRunning.current && status.state === 'cancelled') setCancelled(true);
        }
        sawRunning.current = false;
      }
    });
  }, [base, identity, visible, busy, localBusy]);
  async function generate() {
    if (disabled || busy || localBusy || request.current || !itemIds.length) return;
    const controller = new AbortController(); request.current = controller;
    setLocalBusy(true); setError(''); setCancelled(false);
    tagProgress.begin(base);
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
      tagProgress.end(base);
      if (request.current === controller) { request.current = null; setLocalBusy(false); }
    }
  }
  return <div className="tag-generation">
    <button className="secondary" type="button" disabled={disabled || busy || localBusy || !itemIds.length}
      onClick={() => void generate()}>{localBusy ? 'Generating AI tags…' : 'Generate AI tags'}</button>
    <SettingsHelp title="About AI tag generation">
      <p>Sends this saved version’s item statements or questions and answers to OpenAI through Codex to organize AI tags.</p>
    </SettingsHelp>
    <ErrorBox error={error} />
    <ErrorBox error={progressError} />
    {progressError && <button type="button" className="quiet" onClick={() => void tagProgress.refresh(base)}>Retry progress check</button>}
    {cancelled && <Feedback>{cancelledGenerationMessage}</Feedback>}
    {(busy || localBusy) && <GenerationStatus base={base} kind="tags" label="AI tag generation"
      sharedProgress={shared.progress?.state === 'running' ? shared.progress : null}
      onProgressUpdate={progress => tagProgress.update(base, progress)} />}
  </div>;
}
