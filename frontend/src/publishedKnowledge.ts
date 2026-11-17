import { api } from './api.ts';
import { parseKnowledgeResponse, type KnowledgeResponse } from './knowledge.ts';

export type PublishedKnowledgeState = { data: KnowledgeResponse | null; loading: boolean; error: string };
type Entry = {
  state: PublishedKnowledgeState; listeners: Set<(state: PublishedKnowledgeState) => void>;
  request?: AbortController; promise?: Promise<void>; version: number; loadedAt: number;
};
// An entry lives only while mounted readers for this project exist. Preview
// results never enter this store, and every consumer receives the same snapshot.
const entries = new Map<string, Entry>();
function entryFor(base: string) {
  if (!/^\/projects\/[1-9]\d*$/.test(base)) throw new Error('Invalid project knowledge path.');
  let entry = entries.get(base);
  if (!entry) {
    entry = { state: { data: null, loading: false, error: '' }, listeners: new Set(), version: 0, loadedAt: 0 };
    entries.set(base, entry);
  }
  return entry;
}
function notify(entry: Entry) { for (const listener of entry.listeners) listener(entry.state); }

export function subscribePublishedKnowledge(base: string, listener: (state: PublishedKnowledgeState) => void) {
  const entry = entryFor(base);
  entry.listeners.add(listener);
  listener(entry.state);
  return () => {
    entry.listeners.delete(listener);
    // Effect replacement and StrictMode may immediately subscribe again.
    queueMicrotask(() => {
      if (!entry.listeners.size && entries.get(base) === entry) {
        entry.version++;
        entry.request?.abort();
        entries.delete(base);
      }
    });
  };
}

export function readPublishedKnowledge(base: string, { refresh = false, publicationId }: {
  refresh?: boolean; publicationId?: string;
} = {}): Promise<void> {
  const entry = entryFor(base);
  if (entry.promise) {
    if (!publicationId) return entry.promise;
    // Publication may have advanced while the shared request was in flight.
    // Retry once without a requested identity; a real mismatch stays visible.
    return entry.promise.then(() => {
      if (entries.get(base) === entry && entry.listeners.size && !entry.state.error
          && entry.state.data?.snapshot?.publication_id !== publicationId)
        return readPublishedKnowledge(base, { refresh: true });
    });
  }
  // Adjacent views share a recent read. Reopening a view later still rechecks
  // current publication and evidence availability, without a background timer.
  if (!refresh && entry.state.data && Date.now() - entry.loadedAt < 1500
      && (!publicationId || entry.state.data.snapshot?.publication_id === publicationId))
    return Promise.resolve();
  const controller = new AbortController();
  const version = ++entry.version;
  entry.request = controller;
  entry.state = { ...entry.state, loading: true, error: '' };
  notify(entry);
  const promise = api<unknown>(`${base}/codex/knowledge`, { signal: controller.signal })
    .then(raw => {
      if (controller.signal.aborted || entry.version !== version || entries.get(base) !== entry) return;
      const data = parseKnowledgeResponse(raw, Number(base.split('/')[2]));
      entry.loadedAt = Date.now();
      entry.state = { data, loading: false, error: '' };
      notify(entry);
    })
    .catch(error => {
      if (controller.signal.aborted || entry.version !== version || entries.get(base) !== entry) return;
      entry.state = { ...entry.state, loading: false, error: error instanceof Error ? error.message : 'Could not load project knowledge.' };
      notify(entry);
    })
    .finally(() => {
      if (entry.version === version) { entry.promise = undefined; entry.request = undefined; }
    });
  entry.promise = promise;
  return promise;
}

export function updatePublishedKnowledge(base: string, update: (current: KnowledgeResponse | null) => KnowledgeResponse | null) {
  const entry = entryFor(base);
  const data = update(entry.state.data);
  if (data === entry.state.data) return;
  entry.version++;
  entry.request?.abort();
  entry.promise = undefined;
  entry.request = undefined;
  entry.state = { data, loading: false, error: '' };
  entry.loadedAt = Date.now();
  notify(entry);
}
