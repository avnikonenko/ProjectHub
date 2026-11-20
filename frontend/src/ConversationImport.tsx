import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { conversationCompletenessLabels, conversationImportBody, conversationProviderLabel,
  conversationReference, conversationSourceLabels, parseConversationPreview, readConversationFile,
  type ConversationInput, type ConversationPreview } from './conversationImportData';
import { Feedback } from './Feedback';
import { ErrorBox } from './ui';

type Preview = { base: string; source: ConversationInput; result: ConversationPreview };
type InputMode = 'link' | 'file';
const title = 'Import conversation';

export function ConversationImport({ base, onClose, onImported }: {
  base: string;
  onClose: () => void;
  onImported: () => Promise<void>;
}) {
  const [mode, setMode] = useState<InputMode>('link');
  const [url, setUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [sourceUrl, setSourceUrl] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [confirmPartial, setConfirmPartial] = useState(false);
  const [replaceExisting, setReplaceExisting] = useState(false);
  const [pending, setPending] = useState<'preview' | 'import' | null>(null);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), [base]);

  const current = preview?.base === base ? preview : null;
  const result = current?.result;
  const body = current ? conversationImportBody(current.source, current.result, confirmPartial, replaceExisting) : null;
  const reference = conversationReference(result?.reference);
  const locked = pending === 'import' || saved;

  function resetPreview() {
    active.current?.abort();
    active.current = null;
    setPending(null);
    setPreview(null);
    setConfirmPartial(false);
    setReplaceExisting(false);
    setError('');
  }

  function changeMode(next: InputMode) {
    if (mode === next || locked) return;
    resetPreview();
    setMode(next);
    if (next === 'link') setFile(null);
  }

  async function submit() {
    if (active.current || saved || (current && !body)) return;
    const controller = new AbortController();
    const ownsRequest = () => !controller.signal.aborted && active.current === controller;
    active.current = controller;
    setPending(current ? 'import' : 'preview');
    setError('');
    try {
      if (body) {
        await api(`${base}/conversations/import`, {
          method: 'POST', signal: controller.signal, body: JSON.stringify(body),
        });
        if (!ownsRequest()) return;
        setSaved(true);
        setPreview(null);
        try {
          await onImported();
          if (ownsRequest()) onClose();
        } catch (refreshError) {
          if (ownsRequest()) setError(`Conversation saved. The project could not be refreshed. ${(refreshError as Error).message}`);
        }
      } else {
        let source: ConversationInput;
        if (mode === 'file') {
          if (!file) throw new Error('Choose a JSON or Markdown (.md) conversation file.');
          const content = await readConversationFile(file, controller.signal);
          if (!ownsRequest()) return;
          source = { filename: file.name, content, ...(sourceUrl.trim() ? { source_url: sourceUrl.trim() } : {}) };
        } else {
          if (!url.trim()) return;
          source = { url: url.trim() };
        }
        const raw = await api<unknown>(`${base}/conversations/preview`, {
          method: 'POST', signal: controller.signal, body: JSON.stringify(source),
        });
        if (ownsRequest()) setPreview({ base, source, result: parseConversationPreview(raw) });
      }
    } catch (requestError) {
      if (!ownsRequest()) return;
      const message = requestError instanceof Error ? requestError.message : 'Conversation request failed.';
      if (body) {
        resetPreview();
        setError(`${message} Preview the source again before importing.`);
      } else setError(message);
    } finally {
      if (ownsRequest()) {
        active.current = null;
        setPending(null);
      }
    }
  }

  return <div className="modal-backdrop">
    <section className="modal" role="dialog" aria-modal="true" aria-label={title}>
      <div className="section-title">
        <h2>{title}</h2>
        <button type="button" className="quiet" disabled={pending === 'import'}
          onClick={onClose} aria-label="Close form">×</button>
      </div>
      {saved ? <>{pending === 'import' && <Feedback>Conversation saved. Reloading the project…</Feedback>}
        <ErrorBox error={error} /><button type="button" className="secondary" disabled={pending === 'import'}
        onClick={onClose}>Close</button></> :
        <form onSubmit={event => { event.preventDefault(); if (!current) void submit(); }}>
          <div className="actions left" role="group" aria-label="Conversation source">
            <button type="button" className="secondary" aria-pressed={mode === 'link'} disabled={locked}
              onClick={() => changeMode('link')}>Share link</button>
            <button type="button" className="secondary" aria-pressed={mode === 'file'} disabled={locked}
              onClick={() => changeMode('file')}>Local file</button>
          </div>
          {mode === 'link' ? <label>
            Public conversation link
            <input type="url" required maxLength={2048} value={url} disabled={locked}
              onChange={event => { resetPreview(); setUrl(event.target.value); }} />
          </label> : <>
            <label>
              Conversation file (JSON or Markdown)
              <input type="file" accept=".json,.md" required disabled={locked}
                onChange={event => { resetPreview(); setFile(event.target.files?.[0] ?? null); }} />
            </label>
            <label>
              Original source link (optional)
              <input type="url" maxLength={2048} value={sourceUrl} disabled={locked}
                onChange={event => { resetPreview(); setSourceUrl(event.target.value); }} />
            </label>
          </>}
          <ErrorBox error={error} />
          {error && mode === 'link' && <button type="button" className="secondary" onClick={() => changeMode('file')}>
            Import from file
          </button>}
          {pending === 'preview' && <Feedback>Preparing preview…</Feedback>}
          {pending === 'import' && <Feedback>Saving conversation…</Feedback>}
          {result && <section aria-label="Conversation preview">
            <h3>{result.title}</h3>
            <p>{conversationProviderLabel(result.provider)} · {conversationCompletenessLabels[result.completeness]} · {conversationSourceLabels[result.source_status]}</p>
            <p>{result.message_count.toLocaleString()} {result.message_count === 1 ? 'message' : 'messages'}
              {' · '}{result.role_counts.user.toLocaleString()} from user · {result.role_counts.assistant.toLocaleString()} from assistant</p>
            {reference && <p><a href={reference} target="_blank" rel="noreferrer">Open source conversation</a></p>}
            {!!result.sample.length && <details>
              <summary>Sample messages</summary>
              {result.sample.map((message, index) => <div key={index}>
                <strong>{message.role === 'user' ? 'User' : 'Assistant'}</strong>
                <p className="shared-transcript">{message.text}</p>
              </div>)}
            </details>}
            {result.existing?.duplicate ? <Feedback>This conversation is already saved as “{result.existing.title}”.</Feedback>
              : <>
                {result.existing && <label className="generation-result-choice">
                  <input type="checkbox" checked={replaceExisting} disabled={locked}
                    onChange={event => setReplaceExisting(event.target.checked)} />
                  Replace the saved conversation “{result.existing.title}”
                </label>}
                {result.completeness === 'partial' && <label className="generation-result-choice">
                  <input type="checkbox" checked={confirmPartial} disabled={locked}
                    onChange={event => setConfirmPartial(event.target.checked)} />
                  Import this partial transcript
                </label>}
              </>}
          </section>}
          <div className="actions">
            <button type="button" className="secondary" disabled={pending === 'import'} onClick={onClose}>Cancel</button>
            <button type={current ? 'button' : 'submit'} onClick={current ? () => void submit() : undefined}
              disabled={!!pending || (current ? !body : mode === 'link' ? !url.trim() : !file)}>
              {result ? result.existing && !result.existing.duplicate ? 'Update conversation' : 'Import conversation' : 'Preview conversation'}
            </button>
          </div>
        </form>}
    </section>
  </div>;
}
