import { api } from './api.ts';
import type { Progress } from './GenerationStatus';

export type TagProgressState = { progress: Progress | null; error: string; starting: boolean };
type Listener = (state: TagProgressState) => void;
type Watch = {
  state: TagProgressState; listeners: Set<Listener>; requests: number; version: number;
  controller?: AbortController; promise?: Promise<void>; timer?: ReturnType<typeof setTimeout>; checkedAt: number;
};

/** Share status reads across widgets; idle results never schedule another poll. */
export function createTagProgressMonitor(load: (base: string, signal: AbortSignal) => Promise<Progress>, delayMs = 1500) {
  const watches = new Map<string, Watch>();
  const notify = (watch: Watch) => { for (const listener of watch.listeners) listener(watch.state); };
  function get(base: string) {
    let watch = watches.get(base);
    if (!watch) {
      watch = { state: { progress: null, error: '', starting: false }, listeners: new Set(), requests: 0, version: 0, checkedAt: 0 };
      watches.set(base, watch);
    }
    return watch;
  }
  function dispose(base: string, watch: Watch) {
    if (watch.listeners.size || watch.requests || watches.get(base) !== watch) return;
    watch.version++;
    watch.controller?.abort();
    clearTimeout(watch.timer);
    watches.delete(base);
  }
  function poll(base: string): Promise<void> {
    const watch = get(base);
    if (watch.promise) return watch.promise;
    clearTimeout(watch.timer);
    watch.timer = undefined;
    const controller = new AbortController();
    const version = ++watch.version;
    watch.controller = controller;
    const promise = load(base, controller.signal).then(progress => {
      if (controller.signal.aborted || watch.version !== version || watches.get(base) !== watch) return;
      watch.checkedAt = Date.now();
      watch.state = { progress, error: '', starting: watch.requests > 0 };
      notify(watch);
    }).catch(error => {
      if (controller.signal.aborted || watch.version !== version || watches.get(base) !== watch) return;
      watch.state = { ...watch.state, error: error instanceof Error ? error.message : 'Could not read tag progress.' };
      notify(watch);
    }).finally(() => {
      if (watch.version !== version || watches.get(base) !== watch) return;
      watch.promise = undefined;
      watch.controller = undefined;
      if (watch.requests || watch.state.progress?.state === 'running') {
        watch.timer = setTimeout(() => { void poll(base); }, delayMs);
      }
      dispose(base, watch);
    });
    watch.promise = promise;
    return promise;
  }
  return {
    subscribe(base: string, listener: Listener) {
      const watch = get(base);
      watch.listeners.add(listener);
      listener(watch.state);
      if (!watch.promise && !watch.timer && (!watch.checkedAt || Date.now() - watch.checkedAt >= delayMs)) void poll(base);
      return () => {
        watch.listeners.delete(listener);
        queueMicrotask(() => dispose(base, watch));
      };
    },
    refresh: poll,
    begin(base: string) {
      const watch = get(base);
      watch.requests++;
      watch.state = { ...watch.state, starting: true };
      notify(watch);
      void poll(base);
    },
    end(base: string) {
      const watch = watches.get(base);
      if (!watch) return;
      watch.requests = Math.max(0, watch.requests - 1);
      watch.state = { ...watch.state, starting: watch.requests > 0 };
      notify(watch);
      if (!watch.listeners.size) { dispose(base, watch); return; }
      void poll(base);
    },
    update(base: string, progress: Progress) {
      const watch = get(base);
      watch.version++;
      watch.controller?.abort();
      watch.controller = undefined;
      watch.promise = undefined;
      clearTimeout(watch.timer);
      watch.timer = undefined;
      watch.state = { progress, starting: watch.requests > 0, error: '' };
      notify(watch);
      if (watch.requests || progress.state === 'running')
        watch.timer = setTimeout(() => { void poll(base); }, delayMs);
      dispose(base, watch);
    },
  };
}

export const tagProgress = createTagProgressMonitor((base, signal) =>
  api<Progress>(`${base}/codex/generation/status?kind=tags`, { signal }));
