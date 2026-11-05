export interface CodexMessage {
  id: number;
  text: string;
}

// Completed assistant items are safe to offer as proposals. Tool output and deltas
// remain in the event log and must not be mistaken for completed AI findings.
export function completedMessages(
  events: { id: number; method: string; payload?: any }[],
): CodexMessage[] {
  return events.flatMap((event) => {
    const item = event.payload?.item;
    return event.method === 'item/completed' &&
      item?.type === 'agentMessage' &&
      typeof item.text === 'string'
      ? [{ id: event.id, text: item.text }]
      : [];
  });
}
