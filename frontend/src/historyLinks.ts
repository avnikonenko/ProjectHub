export type HistoryTurnLink = { projectId: number; sessionId: number; turnId: string };

export function historyTurnHref({ projectId, sessionId, turnId }: HistoryTurnLink) {
  return `#history?${new URLSearchParams({
    project: String(projectId), session: String(sessionId), turn: turnId,
  })}`;
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
