export type HistoryGroup<T> =
  | { kind: 'turn'; turn: T }
  | { kind: 'shared'; turns: T[] };

/** Collapse adjacent inherited turns without moving them across unique content. */
export function groupSharedHistory<T extends { id: string; shared_history?: unknown }>(
  turns: T[], collapse = true,
): HistoryGroup<T>[] {
  const groups: HistoryGroup<T>[] = [];
  for (const turn of turns) {
    if (!collapse || !turn.shared_history) {
      groups.push({ kind: 'turn', turn });
      continue;
    }
    const previous = groups[groups.length - 1];
    if (previous?.kind === 'shared') previous.turns.push(turn);
    else groups.push({ kind: 'shared', turns: [turn] });
  }
  return groups;
}
