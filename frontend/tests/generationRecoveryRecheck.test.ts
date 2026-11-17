import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer, type ViteDevServer } from 'vite';

let server: ViteDevServer;
let useGenerationRecovery: any;
// A small hook host executes the real recovery hook without adding a DOM or
// test-renderer dependency. Tests assert requests and observable state changes.
class HookHost {
  includeParameters = false;
  slots: any[] = []; effects: any[] = []; pending: (() => void)[] = [];
  cursor = 0; queued = false; mounted = true; value: any;
  useState = (initial: any) => {
    const index = this.cursor++;
    if (!(index in this.slots)) this.slots[index] = typeof initial === 'function' ? initial() : initial;
    return [this.slots[index], (update: any) => {
      if (!this.mounted) return;
      const value = typeof update === 'function' ? update(this.slots[index]) : update;
      if (Object.is(value, this.slots[index])) return;
      this.slots[index] = value;
      if (!this.queued) {
        this.queued = true;
        queueMicrotask(() => { this.queued = false; if (this.mounted) this.render(); });
      }
    }];
  };
  useEffect = (effect: () => any, dependencies: any[]) => {
    const index = this.cursor++;
    const previous = this.effects[index];
    if (previous && dependencies.every((value, i) => Object.is(value, previous.dependencies[i]))) return;
    this.pending.push(() => {
      previous?.cleanup?.();
      this.effects[index] = { dependencies, cleanup: effect() };
    });
  };
  render() {
    this.cursor = 0; (globalThis as any).__generationRecoveryHooks = this;
    this.value = useGenerationRecovery('/projects/1', { includeBrief: true, includeKnowledge: true, includeParameters: this.includeParameters });
    for (const commit of this.pending.splice(0)) commit();
  }
  dispose() { this.mounted = false; for (const effect of this.effects) effect?.cleanup?.(); }
}
const settle = () => new Promise<void>(resolve => setImmediate(resolve));
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    ssr: { noExternal: ['react'] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom',
    plugins: [{ name: 'generation-hook-host', enforce: 'pre',
      resolveId(id, importer) {
        if (!importer?.endsWith('/src/GenerationStatus.tsx')) return;
        if (id === 'react') return '\0generation-hook-host';
        if (id.startsWith('react/jsx')) return '\0generation-jsx-stub';
        if (id === './Feedback' || id === './ui') return '\0generation-ui-stub';
      },
      load(id) {
        if (id === '\0generation-hook-host') return `export const useState=(...args)=>globalThis.__generationRecoveryHooks.useState(...args);export const useEffect=(...args)=>globalThis.__generationRecoveryHooks.useEffect(...args);export const useRef=value=>({current:value});export const useCallback=fn=>fn;`;
        if (id === '\0generation-ui-stub') return 'export const Feedback=()=>null;export const ErrorBox=()=>null;';
        if (id === '\0generation-jsx-stub') return 'export const jsx=()=>null;export const jsxs=jsx;export const jsxDEV=jsx;export const Fragment=Symbol();';
      },
    }],
  });
  useGenerationRecovery = (await server.ssrLoadModule('/src/GenerationStatus.tsx')).useGenerationRecovery;
});
after(async () => { await server?.close(); delete (globalThis as any).__generationRecoveryHooks; });
function fixture(initialBrief: any = { state: 'idle' }, initialParameters?: any) {
  const original = globalThis.fetch;
  const responses: Record<string, any> = { brief: initialBrief, knowledge: { state: 'idle' }, qa: { state: 'idle' }, scheme: { state: 'idle' }, parameters: initialParameters ?? { state: 'idle' } };
  const calls: { kind: string; method: string }[] = [];
  globalThis.fetch = async (input, options) => {
    const kind = new URL(String(input), 'http://test.local').searchParams.get('kind')!;
    calls.push({ kind, method: options?.method ?? 'GET' });
    return Response.json(responses[kind]);
  };
  const host = new HookHost(); host.includeParameters = initialParameters !== undefined; host.render();
  return { host, responses, calls, dispose() { host.dispose(); globalThis.fetch = original; } };
}
test('opt-in workspace recovery follows Parameters completion without generating another result', async t => {
  const run = fixture({ state: 'idle' }, { state: 'running', started_at: 'parameters-run' });
  try {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    await settle();
    assert.equal(run.host.value.checking, false);
    assert.equal(run.host.value.running.parameters, true);
    run.responses.parameters = { state: 'completed', started_at: 'parameters-run' };
    t.mock.timers.tick(1500); await settle();
    assert.equal(run.host.value.running.parameters, false);
    assert.equal(run.host.value.version.parameters, 1);
    assert.equal(run.host.value.version.brief, 0);
    assert.equal(run.host.value.version.knowledge, 0);
    assert.ok(run.calls.every(call => call.method === 'GET'));
  } finally { run.dispose(); }
});
test('lost POST recovery follows running status to saved completion without another POST', async t => {
  const run = fixture();
  try {
    await settle(); assert.equal(run.host.value.checking, false);
    t.mock.timers.enable({ apis: ['setTimeout'] });
    run.responses.brief = { state: 'running', started_at: 'run-one' };
    run.host.value.recheck('brief'); await settle();
    assert.equal(run.host.value.running.brief, true);
    assert.equal(run.host.value.version.brief, 0);
    run.responses.brief = { state: 'completed', started_at: 'run-one' };
    t.mock.timers.tick(1500); await settle();
    assert.equal(run.host.value.running.brief, false);
    assert.equal(run.host.value.version.brief, 1);
    assert.equal(run.host.value.error, '');
    assert.ok(run.calls.every(call => call.method === 'GET'));
  } finally { run.dispose(); }
});
test('a response already completed before recovery reloads only the requested saved result', async () => {
  const run = fixture();
  try {
    await settle(); run.responses.brief = { state: 'completed', started_at: 'new-finished-run' };
    run.host.value.recheck('brief'); await settle();
    assert.equal(run.host.value.version.brief, 1);
    assert.equal(run.host.value.version.knowledge, 0);
    assert.equal(run.host.value.checking, false);
    assert.ok(run.calls.every(call => call.method === 'GET'));
  } finally { run.dispose(); }
});
test('previous idle status keeps refresh locked through handoff until the new run starts', async t => {
  const run = fixture();
  try {
    await settle(); t.mock.timers.enable({ apis: ['setTimeout'] });
    run.host.value.recheck('brief'); await settle();
    assert.equal(run.host.value.checking, true);
    assert.equal(run.host.value.version.brief, 0);
    run.responses.brief = { state: 'running', started_at: 'delayed-new-run' };
    t.mock.timers.tick(1500); await settle();
    assert.equal(run.host.value.running.brief, true);
    assert.equal(run.host.value.checking, false);
    assert.ok(run.calls.every(call => call.method === 'GET'));
  } finally { run.dispose(); }
});
test('a never-started request has a bounded handoff and failed/cancelled requests remain explicit', async t => {
  const run = fixture();
  try {
    await settle(); t.mock.timers.enable({ apis: ['setTimeout'] });
    run.host.value.recheck('brief'); await settle();
    t.mock.timers.tick(1500); await settle();
    assert.equal(run.host.value.checking, true);
    t.mock.timers.tick(1500); await settle();
    assert.equal(run.host.value.checking, false);
    assert.equal(run.host.value.version.brief, 1);
    for (const state of ['failed', 'cancelled']) {
      run.responses.brief = { state, started_at: state, ...(state === 'failed' ? { error: 'Recorded failure' } : {}) };
      run.host.value.recheck('brief'); await settle();
      assert.equal(run.host.value.running.brief, false);
      assert.equal(run.host.value.checking, false);
      if (state === 'failed') assert.match(run.host.value.error, /Recorded failure/);
      else assert.equal(run.host.value.error, '');
    }
    assert.ok(run.calls.every(call => call.method === 'GET'));
  } finally { run.dispose(); }
});

test('the previous completed run cannot end recovery before a delayed replacement appears', async t => {
  const run = fixture({ state: 'completed', started_at: 'old-run' });
  try {
    await settle(); t.mock.timers.enable({ apis: ['setTimeout'] });
    run.host.value.recheck('brief'); await settle();
    assert.equal(run.host.value.checking, true);
    assert.equal(run.host.value.version.brief, 0);
    run.responses.brief = { state: 'completed', started_at: 'new-run' };
    t.mock.timers.tick(1500); await settle();
    assert.equal(run.host.value.checking, false);
    assert.equal(run.host.value.version.brief, 1);
    assert.ok(run.calls.every(call => call.method === 'GET'));
  } finally { run.dispose(); }
});


test('starting one view does not clear a sibling view recovery error', async () => {
  const run = fixture({ state: 'failed', started_at: 'brief-failed', error: 'Overview failed' });
  try {
    await settle();
    run.responses.qa = { state: 'failed', started_at: 'qa-failed', error: 'Questions failed' };
    run.host.value.recheck('qa'); await settle();
    assert.match(run.host.value.error, /Overview failed/);
    assert.match(run.host.value.error, /Questions failed/);
    run.host.value.clearError('brief'); await settle();
    assert.doesNotMatch(run.host.value.error, /Overview failed/);
    assert.match(run.host.value.error, /Questions failed/);
  } finally { run.dispose(); }
});
