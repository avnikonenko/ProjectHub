import { after, before, test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { createServer, type ViteDevServer } from 'vite';
import type { useProjectGenerationSettings } from '../src/generationSettings';

let server: ViteDevServer;
let useSettings: typeof useProjectGenerationSettings;
let currentHost: Workspace;
const defaults = { model: '', effort: '' };
const previous = { model: 'account-a-model', effort: 'high' };
const settle = () => new Promise<void>(resolve => setImmediate(resolve));

// Run the real hook with mount, cleanup and remount, without a browser dependency.
class Workspace {
  value!: ReturnType<typeof useSettings>;
  slots: unknown[] = [];
  effects: (() => void | (() => void))[] = [];
  cleanups: (() => void)[] = [];
  index = 0;
  active = true;
  mounted = false;
  readonly base: string;
  constructor(base: string) {
    this.base = base;
    this.render();
    this.cleanups = this.effects.map(effect => effect()).filter((cleanup): cleanup is () => void => !!cleanup);
    this.mounted = true;
  }
  useState(initial: unknown) {
    const index = this.index++;
    if (!(index in this.slots)) this.slots[index] = initial;
    return [this.slots[index], (update: any) => {
      if (!this.active) return;
      this.slots[index] = typeof update === 'function' ? update(this.slots[index]) : update;
      queueMicrotask(() => { if (this.active) this.render(); });
    }];
  }
  useRef(initial: unknown) {
    const index = this.index++;
    if (!(index in this.slots)) this.slots[index] = { current: initial };
    return this.slots[index];
  }
  useEffect(effect: () => void | (() => void)) { if (!this.mounted) this.effects.push(effect); }
  render() { currentHost = this; this.index = 0; this.value = useSettings(this.base); }
  dispose() { this.active = false; this.cleanups.forEach(cleanup => cleanup()); }
}

before(async () => {
  (globalThis as any).__settingsHooks = () => currentHost;
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    ssr: { noExternal: ['react'] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom',
    plugins: [{
      name: 'generation-settings-hook-host',
      enforce: 'pre',
      resolveId(id, importer) {
        if (id === 'react' && importer?.endsWith('/src/generationSettings.ts')) return '\0generation-settings-hook-host';
      },
      load(id) {
        if (id === '\0generation-settings-hook-host') return `
          export const useState = initial => globalThis.__settingsHooks().useState(initial);
          export const useRef = initial => globalThis.__settingsHooks().useRef(initial);
          export const useEffect = effect => globalThis.__settingsHooks().useEffect(effect);
        `;
      },
    }],
  });
  useSettings = (await server.ssrLoadModule('/src/generationSettings.ts')).useProjectGenerationSettings;
});
after(async () => { await server.close(); delete (globalThis as any).__settingsHooks; });

function fixture(t: TestContext) {
  const data = new Map<string, string>([['projecthub.ai-settings', JSON.stringify(previous)]]);
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'window', { configurable: true, value: new EventTarget() });
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => data.set(key, value),
    removeItem: (key: string) => data.delete(key),
  } });
  const projects = new Map([['/projects/1', 'account-a'], ['/projects/2', 'account-z']]);
  const workspaces: Workspace[] = [];
  let finish: (response: Response) => void;
  const calls: [string, string][] = [];
  t.mock.method(globalThis, 'fetch', async (url: string, init?: RequestInit) => {
    const base = url.slice(4, -'/codex/account'.length);
    const method = init?.method ?? 'GET';
    calls.push([base, method]);
    if (method === 'PATCH') return new Promise<Response>(resolve => { finish = resolve; });
    return Response.json({ account: projects.get(base), options: [] });
  });
  t.after(() => {
    workspaces.forEach(workspace => workspace.dispose());
    if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow);
    else Reflect.deleteProperty(globalThis, 'window');
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage);
    else Reflect.deleteProperty(globalThis, 'localStorage');
  });
  return {
    projects, calls,
    complete: (response: Response) => finish(response),
    open(id: number) { const workspace = new Workspace(`/projects/${id}`); workspaces.push(workspace); return workspace; },
  };
}

test('model settings restore only for the verified account in the same project', async t => {
  const run = fixture(t);
  const first = run.open(1);
  assert.deepEqual(first.value.generation, defaults);
  await settle();
  assert.deepEqual(first.value.generation, defaults);
  first.value.changeGeneration(previous);
  await settle();
  first.dispose();

  const other = run.open(2);
  const restored = run.open(1);
  assert.deepEqual(restored.value.generation, defaults);
  await settle();
  assert.deepEqual(other.value.generation, defaults);
  assert.deepEqual(restored.value.generation, previous);

  restored.dispose();
  run.projects.set('/projects/1', 'account-b');
  const newAccount = run.open(1);
  await settle();
  assert.equal(newAccount.value.accounts?.account, 'account-b');
  assert.deepEqual(newAccount.value.generation, defaults);
});

test('returning to a project during its account change waits to restore model settings', async t => {
  const run = fixture(t);
  const first = run.open(1);
  await settle();
  first.value.changeGeneration(previous);
  await settle();
  const changing = first.value.changeAccount('account-b');
  await settle();
  assert.deepEqual(first.value.generation, defaults);
  first.dispose();
  const reopened = run.open(1);
  const other = run.open(2);
  await settle();
  assert.deepEqual(reopened.value.generation, defaults);
  assert.equal(Boolean(reopened.value.accounts), false);
  assert.equal(other.value.accounts?.account, 'account-z');
  assert.equal(run.calls.filter(([base, method]) => base === '/projects/1' && method === 'GET').length, 1);

  run.projects.set('/projects/1', 'account-b');
  run.complete(Response.json({ account: 'account-b', options: [] }));
  await changing;
  await settle();
  assert.equal(reopened.value.accounts?.account, 'account-b');
  assert.deepEqual(reopened.value.generation, defaults);
  // If the old account is later restored externally, the old workspace's setting stays cleared.
  run.projects.set('/projects/1', 'account-a');
  const oldAccount = run.open(1);
  await settle();
  assert.deepEqual(oldAccount.value.generation, defaults);
});

test('a rejected account change preserves the model after verifying the original account', async t => {
  const run = fixture(t);
  const workspace = run.open(1);
  await settle();
  workspace.value.changeGeneration(previous);
  await settle();
  const changing = workspace.value.changeAccount('account-b');
  await settle();
  run.complete(Response.json({ detail: 'Generation is active' }, { status: 409 }));
  await changing;
  await settle();
  assert.equal(workspace.value.accountError, 'Generation is active');
  assert.equal(workspace.value.accounts?.account, 'account-a');
  assert.deepEqual(workspace.value.generation, previous);
});

test('Claude generation preferences stay separate from Codex sessions and other projects', async t => {
  const run = fixture(t);
  const workspace = run.open(1);
  await settle();
  workspace.value.changeGeneration(previous);
  workspace.value.changeProvider('claude');
  await settle();
  assert.deepEqual(workspace.value.generation, { ...defaults, provider: 'claude' });
  const claude = { model: 'sonnet', effort: 'medium' };
  workspace.value.changeClaudeProfile('registered:42');
  workspace.value.changeGeneration(claude);
  await settle();
  const selected = { ...claude, provider: 'claude', aiProfile: 'registered:42' };
  assert.deepEqual(workspace.value.generation, selected);
  assert.deepEqual(workspace.value.codexGeneration, previous);
  workspace.dispose();

  const restored = run.open(1);
  const other = run.open(2);
  await settle();
  assert.deepEqual(restored.value.generation, selected);
  assert.equal(other.value.claudeProfile, '');
  assert.deepEqual(other.value.generation, defaults);
  restored.value.changeProvider('codex');
  await settle();
  assert.deepEqual(restored.value.generation, previous);
  restored.value.changeProvider('claude');
  await settle();
  assert.deepEqual(restored.value.generation, selected);
  restored.value.changeClaudeProfile('');
  await settle();
  assert.deepEqual(restored.value.generation, { ...claude, provider: 'claude' });
  restored.dispose();
  const launch = run.open(1);
  await settle();
  assert.deepEqual(launch.value.generation, { ...claude, provider: 'claude' });
});
