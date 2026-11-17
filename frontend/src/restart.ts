export type RestartStatus = {
  available: boolean;
  instance_id: string;
  restarting: boolean;
  reason: string | null;
};

export class RestartResponseError extends Error {}

type RestartOptions = {
  fetch?: typeof fetch;
  now?: () => number;
  delay?: (milliseconds: number, signal: AbortSignal) => Promise<void>;
  timeoutMs?: number;
};

function pause(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, milliseconds);
    signal.addEventListener('abort', abort, { once: true });
  });
}

async function request(method: 'GET' | 'POST', signal: AbortSignal,
  fetcher: typeof fetch, timeoutMs: number): Promise<unknown> {
  signal.throwIfAborted();
  const controller = new AbortController();
  const abort = () => controller.abort(signal.reason);
  signal.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetcher('/api/settings/restart', {
      method, signal: controller.signal, cache: 'no-store',
      ...(method === 'POST' ? { headers: { 'Content-Type': 'application/json' }, body: '{}' } : {}),
    });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try {
        const body = await response.json();
        if (typeof body.detail === 'string') message = body.detail;
      } catch { /* Preserve the HTTP error when its body is not JSON. */ }
      throw new RestartResponseError(message);
    }
    if (method === 'POST' && response.status !== 202) {
      throw new RestartResponseError('The server did not confirm the restart request.');
    }
    try { return await response.json(); }
    catch (error) {
      if (!(error instanceof SyntaxError)) throw error;
      throw new RestartResponseError('The server returned an invalid restart response.');
    }
  } finally {
    clearTimeout(timer);
    signal.removeEventListener('abort', abort);
  }
}

export async function readRestartStatus(signal: AbortSignal, fetcher: typeof fetch = fetch,
  timeoutMs = 3000): Promise<RestartStatus> {
  const value = await request('GET', signal, fetcher, timeoutMs) as Partial<RestartStatus> | null;
  signal.throwIfAborted();
  if (!value || typeof value.available !== 'boolean' || typeof value.restarting !== 'boolean'
    || typeof value.instance_id !== 'string' || !value.instance_id
    || !(value.reason === null || typeof value.reason === 'string')) {
    throw new RestartResponseError('The server returned an invalid restart status.');
  }
  return value as RestartStatus;
}

/** Send exactly one restart request, then wait for a ready, different process. */
export async function restartProjectHub(instanceId: string, signal: AbortSignal,
  options: RestartOptions = {}): Promise<RestartStatus> {
  const fetcher = options.fetch ?? fetch;
  const now = options.now ?? Date.now;
  const delay = options.delay ?? pause;
  const deadline = now() + (options.timeoutMs ?? 60_000);
  const requestBudget = () => Math.max(1, Math.min(3000, deadline - now()));
  signal.throwIfAborted();
  try {
    const acknowledgement = await request('POST', signal, fetcher, requestBudget()) as
      { status?: unknown; instance_id?: unknown } | null;
    if (acknowledgement?.status !== 'restarting' || acknowledgement.instance_id !== instanceId) {
      throw new RestartResponseError('The server did not confirm a restart of the current instance. Reload restart status.');
    }
  } catch (error) {
    signal.throwIfAborted();
    if (error instanceof RestartResponseError) throw error;
    // The process may have stopped before its acknowledgement reached this browser.
  }
  while (now() < deadline) {
    signal.throwIfAborted();
    try {
      const status = await readRestartStatus(signal, fetcher, requestBudget());
      if (now() < deadline && status.instance_id !== instanceId && status.available && !status.restarting) {
        return status;
      }
    } catch (error) {
      signal.throwIfAborted();
      if (error instanceof RestartResponseError) throw error;
    }
    if (now() < deadline) await delay(Math.min(1000, deadline - now()), signal);
  }
  throw new Error('Could not confirm a restarted ProjectHub within 60 seconds. Check the server, then reload restart status.');
}
