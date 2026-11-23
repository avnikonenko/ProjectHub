import type { RecordData } from './types';

export type ClaudeSessionInput = { session_id: string; profile?: string; title?: string };
export type ConversationInput = { url: string } | { filename: string; content: string; source_url?: string } | ClaudeSessionInput;
export type ConversationSummary = {
  provider: string;
  completeness: 'partial' | 'unknown' | 'complete';
  source_status: 'verified' | 'provided' | 'none';
};
export type ConversationPreview = ConversationSummary & {
  title: string;
  reference: string | null;
  message_count: number;
  role_counts: { user: number; assistant: number };
  sample: { role: 'user' | 'assistant'; text: string }[];
  fingerprint: string;
  existing: { id: number; title: string; duplicate: boolean } | null;
};
export type ConversationSource = { id: number; filename: string; created_at: string; content_hash: string };
const MAX_FILE_BYTES = 16 * 1024 * 1024;
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const count = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const name = (value: unknown): value is string => typeof value === 'string' && !!value.trim();
const hasSample = (value: unknown): value is ConversationPreview['sample'] =>
  Array.isArray(value) && value.every(message => object(message)
    && (message.role === 'user' || message.role === 'assistant') && typeof message.text === 'string');
const isSummary = (value: unknown): value is ConversationSummary & Record<string, unknown> => object(value) && name(value.provider)
  && (value.completeness === 'partial' || value.completeness === 'unknown' || value.completeness === 'complete')
  && (value.source_status === 'verified' || value.source_status === 'provided' || value.source_status === 'none');

export function parseConversationPreview(raw: unknown): ConversationPreview {
  if (!object(raw) || !isSummary(raw) || !name(raw.title)
    || (raw.reference !== null && typeof raw.reference !== 'string')
    || !count(raw.message_count) || raw.message_count === 0 || !name(raw.fingerprint)
    || !object(raw.role_counts) || !count(raw.role_counts.user) || !count(raw.role_counts.assistant)
    || raw.role_counts.user + raw.role_counts.assistant !== raw.message_count || !hasSample(raw.sample)
    || (raw.existing !== null && (!object(raw.existing) || !count(raw.existing.id) || raw.existing.id === 0
      || !name(raw.existing.title) || typeof raw.existing.duplicate !== 'boolean'))) {
    throw new Error('The conversation preview returned invalid data. Preview the source again.');
  }
  return raw as ConversationPreview;
}

export function parseCodexSessionPreview(raw: unknown) {
  if (!object(raw) || !name(raw.title) || !hasSample(raw.sample))
    throw new Error('The session preview returned invalid data. Preview the session again.');
  return { title: raw.title, sample: raw.sample };
}

export function conversationImportBody(source: ConversationInput, preview: ConversationPreview,
  confirmPartial: boolean, replaceExisting: boolean) {
  if (preview.existing?.duplicate || (preview.completeness === 'partial' && !confirmPartial)
    || (!!preview.existing && !replaceExisting)) return null;
  return { ...source, fingerprint: preview.fingerprint,
    ...(preview.completeness === 'partial' ? { confirm_partial: true } : {}),
    ...(preview.existing ? { replace_existing: true } : {}) };
}

export async function readConversationFile(file: File, signal: AbortSignal) {
  signal.throwIfAborted();
  if (!/\.(json|md)$/i.test(file.name)) throw new Error('Choose a JSON or Markdown (.md) conversation file.');
  if (file.size === 0) throw new Error('The conversation file is empty. Choose a JSON or Markdown export with messages.');
  if (file.size > MAX_FILE_BYTES) throw new Error('Choose a conversation file up to 16 MiB.');
  const bytes = await file.arrayBuffer();
  signal.throwIfAborted();
  try {
    return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch {
    throw new Error('The file is not valid UTF-8. Choose a UTF-8 JSON or Markdown conversation export.');
  }
}

const legacyChatGPTShare = /^https:\/\/chatgpt\.com\/share\/[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i;
export function importedConversation(note: RecordData): ConversationSummary | null {
  if (note.kind !== 'document') return null;
  if (isSummary(note.conversation)) return note.conversation;
  return typeof note.reference === 'string' && legacyChatGPTShare.test(note.reference)
    ? { provider: 'chatgpt', completeness: 'unknown', source_status: 'provided' }
    : null;
}

export function linkedClaudeSession(note: RecordData): ClaudeSessionInput | null {
  const session = importedConversation(note)?.provider === 'claude' ? note.conversation?.linked_session : null;
  if (!object(session) || session.provider !== 'claude' || typeof session.id !== 'string'
    || !/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(session.id)
    || typeof session.profile !== 'string' || !/^(launch|registered:[1-9]\d*)$/.test(session.profile)) return null;
  return { session_id: session.id, profile: session.profile, ...(note.title ? { title: note.title } : {}) };
}

export function hasConversationMetadata(note: RecordData) {
  return note.kind === 'document' && isSummary(note.conversation);
}

export function conversationSourcesPath(base: string, note: RecordData) {
  const project = /^\/projects\/([1-9]\d*)$/.exec(base);
  const projectId = Number(project?.[1]);
  if (!Number.isSafeInteger(projectId) || !count(note.id) || note.id === 0
    || (note.project_id !== undefined && note.project_id !== projectId)) {
    throw new Error('This conversation does not belong to the selected project.');
  }
  return `${base}/conversations/${note.id}/sources`;
}

export function parseConversationSources(raw: unknown): ConversationSource[] {
  if (!Array.isArray(raw) || raw.some(value => !object(value) || !count(value.id) || value.id === 0
    || !name(value.filename) || !name(value.created_at) || typeof value.content_hash !== 'string'
    || !/^[a-f0-9]{64}$/.test(value.content_hash))
    || new Set(raw.map(value => value.id)).size !== raw.length) {
    throw new Error('The conversation source copies could not be read.');
  }
  return raw;
}

export function conversationReference(reference: unknown) {
  if (typeof reference !== 'string') return null;
  try {
    const url = new URL(reference);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

export function conversationProviderLabel(provider: string) {
  switch (provider.toLowerCase()) {
    case 'chatgpt': return 'ChatGPT';
    case 'claude': return 'Claude';
    case 'gemini': return 'Gemini';
    default: return 'Imported conversation';
  }
}

export const conversationCompletenessLabels = {
  complete: 'Complete', partial: 'Partial', unknown: 'Completeness unknown',
} satisfies Record<ConversationSummary['completeness'], string>;
export const conversationSourceLabels = {
  verified: 'Source verified', provided: 'Source link supplied', none: 'No source link',
} satisfies Record<ConversationSummary['source_status'], string>;
