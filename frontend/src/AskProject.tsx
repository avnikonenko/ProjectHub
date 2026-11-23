import { useEffect, useRef, useState } from 'react';
import { AIEmphasis } from './AIEmphasis';
import { api } from './api';
import { BriefSources, briefReferences, type BriefSource } from './BriefReferences';
import { GenerationStatus, isGenerationCancelled } from './GenerationStatus';
import { generationBody, generationDestination, generationProviderLabels, type Generation, type GenerationProvider } from './generationProvider';
import { ErrorBox } from './ui';

type ProjectAnswer = { answer: string; sources: BriefSource[]; provider?: GenerationProvider };

export type SchemeAnswerContext = {
  kind: 'scheme' | 'focused_scheme';
  generated_at: string;
  revision_id?: number;
};

export function AskProject({ base, projectId, generation, visible, schemeContext, disabled, onBusyChange }: {
  base: string; projectId: number; generation: Generation; visible: boolean;
  schemeContext?: SchemeAnswerContext;
  disabled?: boolean;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [question, setQuestion] = useState('');
  const [result, setResult] = useState<ProjectAnswer | null>(null);
  const [answeredQuestion, setAnsweredQuestion] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [remoteBusy, setRemoteBusy] = useState(false);
  const [requestId, setRequestId] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function ask() {
    const query = question.trim();
    if (!query || disabled || request.current || remoteBusy) return;
    const controller = new AbortController();
    // getRandomValues is also available when the app is served over local-network HTTP.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
    const id = [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
    request.current = controller;
    setRequestId(id);
    setBusy(true);
    onBusyChange?.(true);
    setError('');
    try {
      const response = await api<ProjectAnswer>(`${base}/codex/ask`, {
        method: 'POST',
        body: JSON.stringify({
          question: query, confirmed: true, request_id: id, ...generationBody(generation),
          ...(schemeContext ? { scheme_context: schemeContext } : {}),
        }),
        signal: controller.signal,
      });
      if (!controller.signal.aborted) {
        setResult(response);
        setAnsweredQuestion(query);
      }
    } catch (e) {
      if (!controller.signal.aborted && !isGenerationCancelled(e)) setError((e as Error).message);
    } finally {
      if (request.current === controller) request.current = null;
      if (!controller.signal.aborted) {
        setBusy(false);
        onBusyChange?.(false);
      }
    }
  }

  const prefix = schemeContext ? `${projectId}-ask-${schemeContext.kind}` : `${projectId}-ask`;
  const numbers = new Map(result?.sources.map((source, index) => [source.id, index + 1]));
  return (
    <section className="card project-brief">
      {schemeContext ? (
        <p className="brief-note">
          Uses {schemeContext.kind === 'focused_scheme' ? 'the Focused Scheme' : 'the Scheme'} saved on{' '}
          <time dateTime={schemeContext.generated_at}>{new Date(schemeContext.generated_at).toLocaleString()}</time>.
          ProjectHub sends your question, diagram context, and selected evidence to {generationDestination(generation.provider)}.
        </p>
      ) : (
        <>
          <h2>Ask Project</h2>
          <p className="brief-note">
            ProjectHub searches saved conversations and notes locally. Your question and matching passages
            are sent to {generationDestination(generation.provider)} to generate an answer. The answer is not saved.
          </p>
        </>
      )}
      <form aria-busy={busy || remoteBusy} onSubmit={(event) => { event.preventDefault(); void ask(); }}>
        <label>
          {schemeContext ? 'Question about this Scheme' : 'Project question'}
          <textarea rows={3} required maxLength={2000} value={question} disabled={disabled || busy || remoteBusy}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder={schemeContext ? 'Where does this handoff happen?' : 'What have we already tried?'} />
        </label>
        <button disabled={disabled || busy || remoteBusy || !question.trim()}>{busy ? 'Asking…' : schemeContext ? 'Ask' : 'Ask Project'}</button>
      </form>
      {(visible || busy) && <GenerationStatus base={base} kind="ask" label={schemeContext ? 'Scheme answer' : 'Project answer'}
        askRequestId={requestId} onRunningChange={setRemoteBusy} />}
      <ErrorBox error={error} />
      {result && (
        <section className="brief-section">
          <h3>{busy ? 'Previous answer' : 'Answer'}</h3>
          {result.provider && generationProviderLabels[result.provider] &&
            <small className="muted">Generated by {generationProviderLabels[result.provider]}</small>}
          {answeredQuestion && <p className="muted">{answeredQuestion}</p>}
          <p className="shared-transcript"><AIEmphasis text={result.answer} renderText={text => text.split(/(\[\d+\])/g).map((part, index) => {
            const id = /^\[(\d+)\]$/.exec(part)?.[1];
            return id && numbers.has(id)
              ? <span key={index}>{briefReferences([id], numbers, prefix)}</span>
              : part;
          })} /></p>
          {!!result.sources.length && (
            <details className="brief-sources">
              <summary>Sources ({result.sources.length})</summary>
              <BriefSources sources={result.sources} prefix={prefix} openInNewTab />
            </details>
          )}
        </section>
      )}
    </section>
  );
}
