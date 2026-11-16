export interface CodexMessage {
  id: number;
  text: string;
  created_at?: string;
}

// Completed assistant items are safe to offer as proposals. Tool output and deltas
// remain in the event log and must not be mistaken for completed AI findings.
export function completedMessages(
  events: { id: number; method: string; payload?: any; created_at?: unknown }[],
): CodexMessage[] {
  return events.flatMap((event) => {
    const item = event.payload?.item;
    return event.method === 'item/completed' &&
      item?.type === 'agentMessage' &&
      typeof item.text === 'string'
      ? [{ id: event.id, text: item.text,
        ...(typeof event.created_at === 'string' && Number.isFinite(Date.parse(event.created_at))
          ? { created_at: event.created_at } : {}) }]
      : [];
  });
}
