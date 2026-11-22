import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { conversationCompletenessLabels, conversationImportBody, parseCodexSessionPreview, parseConversationPreview,
  type ClaudeSessionInput, type ConversationPreview } from './conversationImportData';
import { Feedback } from './Feedback';
import type { RecordData } from './types';
import { ErrorBox } from './ui';

type Provider = 'codex' | 'claude';
type ProfileOptions = { options: { id: string; label: string }[]; launch_label: string };
type CodexInput = { thread_id: string; profile?: string; title?: string };
type Sample = ConversationPreview['sample'];
type Preview =
  | { provider: 'codex'; input: CodexInput; result: { title: string; sample: Sample } }
  | { provider: 'claude'; input: ClaudeSessionInput; result: ConversationPreview };
type Refresh = { noteId: number; input: ClaudeSessionInput };

export function SessionLink({ base, refresh, onSaved, onClose }: {
  base: string;
  refresh?: Refresh;
  onSaved: (provider: Provider, record: RecordData) => Promise<void>;
  onClose: () => void;
}) {
  const heading = refresh ? 'Refresh Claude Code session' : 'Link session';
  const [provider, setProvider] = useState<Provider>(refresh ? 'claude' : 'codex');
  const [id, setId] = useState(refresh?.input.session_id ?? '');
  const [title, setTitle] = useState(refresh?.input.title ?? '');
  const [profile, setProfile] = useState(refresh?.input.profile ?? '');
  const [profiles, setProfiles] = useState<ProfileOptions | null>(null);
  const [profileError, setProfileError] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [confirmPartial, setConfirmPartial] = useState(false);
  const [replaceExisting, setReplaceExisting] = useState(false);
  const [pending, setPending] = useState<'preview' | 'save' | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), [base]);
  useEffect(() => {
    if (refresh) return;
    const controller = new AbortController();
    setProfiles(null);
    setProfileError('');
    api<ProfileOptions>(`/settings/ai-profiles?provider=${provider}`, { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setProfiles(result); })
      .catch((e: Error) => { if (!controller.signal.aborted) setProfileError(e.message); });
    return () => controller.abort();
  }, [provider, refresh]);

  const locked = pending !== null || saved;
  const claude = preview?.provider === 'claude' ? preview.result : null;
  const saveBody = preview?.provider === 'claude'
    ? conversationImportBody(preview.input, preview.result, confirmPartial, replaceExisting)
    : preview?.input;

  function resetPreview() {
    setPreview(null);
    setConfirmPartial(false);
    setReplaceExisting(false);
    setError('');
  }

  async function submit() {
    if (active.current || saved || !id.trim() || (preview && !saveBody)) return;
    const controller = new AbortController();
    const owns = () => active.current === controller && !controller.signal.aborted;
    active.current = controller;
    setError('');
    setPending(preview ? 'save' : 'preview');
    const send = <T,>(path: string, input: unknown) => api<T>(`${base}${path}`, {
      method: 'POST', signal: controller.signal, body: JSON.stringify(input),
    });
    try {
      if (preview) {
        const record = await send<RecordData>(preview.provider === 'codex' ? '/codex/sessions' : '/conversations/import', saveBody);
        if (!owns()) return;
        setSaved(true);
        setPreview(null);
        await onSaved(preview.provider, record);
        if (owns()) onClose();
        return;
      }
      const options = { ...(title.trim() ? { title: title.trim() } : {}), ...(profile ? { profile } : {}) };
      if (provider === 'codex') {
        const input = { thread_id: id.trim(), ...options };
        const result = parseCodexSessionPreview(await send<unknown>('/codex/sessions/preview', input));
        if (owns()) setPreview({ provider, input, result });
      } else {
        const input = { session_id: id.trim(), ...options };
        const result = parseConversationPreview(await send<unknown>('/conversations/preview', input));
        if (refresh && result.existing?.id !== refresh.noteId)
          throw new Error('The preview does not match this saved source. Reload Sources before refreshing.');
        if (owns()) setPreview({ provider, input, result });
      }
    } catch (e) {
      if (!owns()) return;
      const message = e instanceof Error ? e.message : 'Session request failed.';
      if (preview && saveBody) resetPreview();
      setError(message);
    } finally {
      if (owns()) {
        active.current = null;
        setPending(null);
      }
    }
  }

  return <div className="modal-backdrop">
    <section className="modal" role="dialog" aria-modal="true" aria-label={heading}>
      <div className="section-title">
        <h2>{heading}</h2>
        <button type="button" className="quiet" disabled={pending === 'save'} onClick={onClose} aria-label="Close form">×</button>
      </div>
      <form onSubmit={event => { event.preventDefault(); void submit(); }}>
        {!refresh && <>
          <label>Provider
            <select value={provider} disabled={locked} onChange={event => {
              resetPreview(); setProfile(''); setProvider(event.target.value === 'claude' ? 'claude' : 'codex');
            }}>
              <option value="codex">Codex</option>
              <option value="claude">Claude Code</option>
            </select>
          </label>
          <label>Session ID
            <input required maxLength={200} value={id} disabled={locked}
              onChange={event => { resetPreview(); setId(event.target.value); }} />
          </label>
          <label>Profile
            <select value={profile} disabled={locked}
              onChange={event => { resetPreview(); setProfile(event.target.value); }}>
              {provider === 'claude' && <option value="">Find automatically</option>}
              <option value={provider === 'claude' ? 'launch' : ''}>{profiles?.launch_label ?? 'Launch profile'}</option>
              {profiles?.options.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
            </select>
          </label>
          {profileError && <p className="muted">Could not load registered profiles.</p>}
          <label>Session name (optional)
            <input maxLength={200} value={title} disabled={locked}
              onChange={event => { resetPreview(); setTitle(event.target.value); }} />
          </label>
        </>}
        {refresh && <p>Check the local transcript for new messages. Earlier saved copies stay available.</p>}
        <ErrorBox error={error} />
        {saved && <Feedback>{refresh ? 'Session refreshed.' : 'Session linked.'}</Feedback>}
        {pending && !saved && <Feedback>{pending === 'preview' ? 'Preparing session preview…' : 'Saving session…'}</Feedback>}
        {preview && <section aria-label="Session preview">
          <h3>{preview.result.title}</h3>
          {claude && <p>{conversationCompletenessLabels[claude.completeness]} · {claude.message_count.toLocaleString()} messages</p>}
          {!!preview.result.sample.length && <details>
            <summary>Sample messages</summary>
            {preview.result.sample.map((message, index) => <div key={index}>
              <strong>{message.role === 'user' ? 'User' : 'Assistant'}</strong>
              <p className="shared-transcript">{message.text}</p>
            </div>)}
          </details>}
          {claude?.existing?.duplicate ? <Feedback>{refresh ? 'This session is up to date.' : 'This session is already saved in this project.'}</Feedback> : <>
            {claude?.existing && <label className="generation-result-choice">
              <input type="checkbox" checked={replaceExisting} disabled={locked}
                onChange={event => setReplaceExisting(event.target.checked)} />
              Update the saved conversation “{claude.existing.title}”
            </label>}
            {claude?.completeness === 'partial' && <label className="generation-result-choice">
              <input type="checkbox" checked={confirmPartial} disabled={locked}
                onChange={event => setConfirmPartial(event.target.checked)} />
              Save this partial transcript
            </label>}
          </>}
        </section>}
        <div className="actions">
          <button type="button" className="secondary" disabled={pending === 'save'} onClick={onClose}>
            {saved || claude?.existing?.duplicate ? 'Close' : 'Cancel'}
          </button>
          {!saved && !claude?.existing?.duplicate && <button type="submit" disabled={locked || !id.trim() || (!!preview && !saveBody)}>
            {preview ? refresh ? 'Save update' : 'Link session' : 'Preview session'}
          </button>}
        </div>
      </form>
    </section>
  </div>;
}
