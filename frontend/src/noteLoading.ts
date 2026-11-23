import { api } from './api.ts';
import type { RecordData } from './types';

function noteTarget(base: string, note: RecordData) {
  const match = /^\/projects\/([1-9]\d*)$/.exec(base);
  const projectId = match ? Number(match[1]) : NaN;
  if (!Number.isSafeInteger(projectId) || !Number.isSafeInteger(note.id) || note.id < 1
    || (note.project_id !== undefined && note.project_id !== projectId)) {
    throw new Error('This note does not belong to the selected project.');
  }
  return projectId;
}

export function validateLoadedNote(raw: unknown, projectId: number, noteId: number): RecordData {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('The saved note returned an invalid response.');
  }
  const note = raw as RecordData;
  if (note.id !== noteId || note.project_id !== projectId || typeof note.content !== 'string'
    || note.content_loaded === false) {
    throw new Error('The saved note returned invalid or unrelated content.');
  }
  return note;
}

export function noteRequestIdentity(base: string, note: RecordData): string {
  return JSON.stringify([base, note.id, note.project_id, note.updated_at]);
}

export async function loadNote(base: string, note: RecordData, signal?: AbortSignal,
  transcriptCreatedAt?: string | null): Promise<RecordData> {
  const projectId = noteTarget(base, note);
  signal?.throwIfAborted();
  if (transcriptCreatedAt === undefined && typeof note.content === 'string' && note.content_loaded !== false) {
    return validateLoadedNote(note, projectId, note.id);
  }
  const params = new URLSearchParams();
  if (transcriptCreatedAt !== undefined) {
    params.set('transcript', 'true');
    if (transcriptCreatedAt !== null) params.set('created_at', transcriptCreatedAt);
  }
  const value = await api<unknown>(`${base}/notes/${note.id}${params.size ? '?' + params : ''}`, { signal });
  // A mock, proxy, or completed fetch may ignore an abort while decoding JSON.
  signal?.throwIfAborted();
  const loaded = validateLoadedNote(value, projectId, note.id);
  if (typeof transcriptCreatedAt === 'string' && loaded.created_at !== transcriptCreatedAt) {
    throw new Error('The original conversation is no longer available.');
  }
  return loaded;
}
