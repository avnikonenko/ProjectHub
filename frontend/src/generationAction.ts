export type GenerationActionStatus = {
  limit: number;
  total_tokens: number | null;
  reported_requests: number;
  model_requests: number;
  reached: boolean;
  active_jobs?: number;
};

/** Generate one identity for a click, including pages served on a local network. */
export function createGenerationActionId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
}

export function parseGenerationActionStatus(raw: unknown): GenerationActionStatus {
  const validCount = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
  if (raw && typeof raw === 'object') {
    const status = raw as Partial<GenerationActionStatus>;
    if (validCount(status.limit) && (status.total_tokens === null || validCount(status.total_tokens))
      && validCount(status.reported_requests) && validCount(status.model_requests)
      && status.reported_requests <= status.model_requests && typeof status.reached === 'boolean'
      && (status.active_jobs === undefined || validCount(status.active_jobs)))
      return status as GenerationActionStatus;
  }
  throw new Error('Invalid generation token usage.');
}

function pause(signal: AbortSignal) {
  return new Promise<void>(resolve => {
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', done);
      resolve();
    };
    const timer = setTimeout(done, 1500);
    signal.addEventListener('abort', done, { once: true });
    if (signal.aborted) done();
  });
}

/** Keep the last valid report through unavailable reads and wait briefly across job handoffs. */
export async function observeGenerationAction(signal: AbortSignal, pending: () => boolean,
  read: (signal: AbortSignal) => Promise<unknown>, onUsage: (usage: GenerationActionStatus) => void,
  wait: (signal: AbortSignal) => Promise<void> = pause) {
  let idleReads = 0;
  let unavailableReads = 0;
  while (!signal.aborted) {
    try {
      const status = parseGenerationActionStatus(await read(signal));
      if (signal.aborted) return 'aborted';
      onUsage(status);
      unavailableReads = 0;
      idleReads = pending() || status.active_jobs ? 0 : idleReads + 1;
      if (idleReads >= 3) return 'finished';
    } catch {
      if (signal.aborted) return 'aborted';
      idleReads = 0;
      unavailableReads = pending() ? 0 : unavailableReads + 1;
      if (unavailableReads >= 8) return 'unavailable';
    }
    await wait(signal);
  }
  return 'aborted';
}
