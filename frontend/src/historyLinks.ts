export type HistoryTurnLink = { projectId: number; sessionId: number; turnId: string };
export type ChatGPTTranscriptLink = { projectId: number; noteId: number; query?: string };
export type HistoryLink = HistoryTurnLink | ChatGPTTranscriptLink;

export function historyTurnHref({ projectId, sessionId, turnId }: HistoryTurnLink) {
  return `#history?${new URLSearchParams({
    project: String(projectId), session: String(sessionId), turn: turnId,
  })}`;
}

export function chatGPTTranscriptHref({ projectId, noteId, query }: ChatGPTTranscriptLink) {
  const params = new URLSearchParams({ project: String(projectId), note: String(noteId) });
  if (query) params.set('q', query);
  return `#history?${params}`;
}

export function readHistoryLink(hash: string): HistoryLink | null {
  if (!hash.startsWith('#history?')) return null;
  const params = new URLSearchParams(hash.slice('#history?'.length));
  if (!params.has('note')) return readHistoryTurnLink(hash);
  const projectId = Number(params.get('project'));
  const noteId = Number(params.get('note'));
  if (!Number.isSafeInteger(projectId) || projectId < 1 ||
      !Number.isSafeInteger(noteId) || noteId < 1) return null;
  return { projectId, noteId, query: params.get('q')?.slice(0, 2000) || undefined };
}

export function readHistoryTurnLink(hash: string): HistoryTurnLink | null {
  if (!hash.startsWith('#history?')) return null;
  const query = new URLSearchParams(hash.slice('#history?'.length));
  const projectId = Number(query.get('project'));
  const sessionId = Number(query.get('session'));
  const turnId = query.get('turn');
  if (!Number.isSafeInteger(projectId) || projectId < 1 ||
      !Number.isSafeInteger(sessionId) || sessionId < 1 || !turnId) return null;
  return { projectId, sessionId, turnId };
}

export function transcriptMatch(text: string, query?: string) {
  const normalize = (word: string) => word.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
  const words = new Set((query?.match(/[\p{L}\p{N}\p{M}]+/gu) ?? []).slice(0, 30).map(normalize));
  if (words.size) {
    for (const word of text.matchAll(/[\p{L}\p{N}\p{M}]+/gu)) {
      if (words.has(normalize(word[0]))) return { start: word.index, length: word[0].length };
    }
  }
  return null;
}
