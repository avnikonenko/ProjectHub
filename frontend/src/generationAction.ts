import { projectGenerationKinds, type ProjectGenerationKind } from './generationBatch.ts';

export type GenerationActionStatus = {
  limit: number;
  total_tokens: number | null;
  reported_requests: number;
  model_requests: number;
  reached: boolean;
  active_jobs?: number;
};

export type RememberedGenerationAction = {
  id: string;
  kinds: ProjectGenerationKind[];
  startedAt: number;
};

const actionStorageKey = 'projecthub.generation-actions';
const actionMaxAge = 6 * 60 * 60 * 1000;
const maxStoredActions = 32;
export const maxProjectGenerationActions = 8;
const actionIdPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
type ActionStorage = Pick<Storage, 'getItem' | 'setItem'>;
type StoredAction = RememberedGenerationAction & { projectId: number };

function readRememberedActions(storage: ActionStorage, now: number) {
  const parsed: unknown = JSON.parse(storage.getItem(actionStorageKey) ?? '[]');
  if (!Array.isArray(parsed)) return [];
  const ids = new Set<string>();
  return parsed.slice(-maxStoredActions).filter((row): row is StoredAction => {
    if (!row || !Number.isSafeInteger(row.projectId) || row.projectId < 1
      || typeof row.id !== 'string' || !actionIdPattern.test(row.id) || ids.has(row.id)
      || !Number.isSafeInteger(row.startedAt) || row.startedAt > now || row.startedAt <= now - actionMaxAge
      || !Array.isArray(row.kinds) || !row.kinds.length || row.kinds.length > projectGenerationKinds.length
      || new Set(row.kinds).size !== row.kinds.length
      || !row.kinds.every((kind: ProjectGenerationKind) => projectGenerationKinds.includes(kind))) return false;
    ids.add(row.id);
    return true;
  });
}

export function restoreGenerationActions(projectId: number, storage?: ActionStorage, now = Date.now()): RememberedGenerationAction[] {
  try {
    return readRememberedActions(storage ?? sessionStorage, now).filter(row => row.projectId === projectId)
      .slice(-maxProjectGenerationActions).map(({ id, kinds, startedAt }) => ({ id, kinds, startedAt }));
  } catch {
    return [];
  }
}

export function rememberGenerationAction(projectId: number, action: RememberedGenerationAction,
  retainedIds: string[], storage?: ActionStorage) {
  try {
    const target = storage ?? sessionStorage;
    const previous = readRememberedActions(target, action.startedAt)
      .filter(row => row.projectId !== projectId || retainedIds.includes(row.id));
    target.setItem(actionStorageKey, JSON.stringify([...previous, { ...action, projectId }].slice(-maxStoredActions)));
  } catch {
    // Storage is optional. Usage is still available in this workspace.
  }
}

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

function pause(signal: AbortSignal, delay: number) {
  return new Promise<void>(resolve => {
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', done);
      resolve();
    };
    const timer = setTimeout(done, delay);
    signal.addEventListener('abort', done, { once: true });
    if (signal.aborted) done();
  });
}

/** Keep the last valid report through unavailable reads and wait briefly across job handoffs. */
export async function observeGenerationAction(signal: AbortSignal, pending: () => boolean,
  read: (signal: AbortSignal) => Promise<unknown>, onUsage: (usage: GenerationActionStatus) => void,
  wait: (signal: AbortSignal, delay: number) => Promise<void> = pause) {
  let idleReads = 0;
  let unavailableReads = 0;
  let activeJobs = false;
  while (!signal.aborted) {
    try {
      const status = parseGenerationActionStatus(await read(signal));
      if (signal.aborted) return 'aborted';
      onUsage(status);
      unavailableReads = 0;
      activeJobs = !!status.active_jobs;
      idleReads = pending() || activeJobs ? 0 : idleReads + 1;
      if (idleReads >= 3) return 'finished';
    } catch {
      if (signal.aborted) return 'aborted';
      idleReads = 0;
      unavailableReads = pending() ? 0 : unavailableReads + 1;
      if (unavailableReads >= (activeJobs ? 12 : 8)) return 'unavailable';
    }
    await wait(signal, activeJobs ? Math.min(1500 * 2 ** unavailableReads, 10_000) : 1500);
  }
  return 'aborted';
}
