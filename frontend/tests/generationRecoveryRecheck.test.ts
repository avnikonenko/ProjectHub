import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer, type ViteDevServer } from 'vite';

let server: ViteDevServer;
let useGenerationRecovery: any;
let useFocusedSchemeRecovery: any;
let Scheme: any;
let RevisionHistory: any;
// A small hook host executes the real recovery hook without adding a DOM or
// test-renderer dependency. Tests assert requests and observable state changes.
class HookHost {
  includeParameters = false;
  focusedVisible: boolean | undefined;
  renderView?: () => any;
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
  useRef = (initial: any) => {
    const index = this.cursor++;
    return this.slots[index] ??= { current: initial };
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
    this.value = this.renderView ? this.renderView() : this.focusedVisible === undefined
      ? useGenerationRecovery('/projects/1', { includeBrief: true, includeKnowledge: true, includeParameters: this.includeParameters })
      : useFocusedSchemeRecovery('/projects/1', this.focusedVisible);
    for (const commit of this.pending.splice(0)) commit();
  }
  dispose() { this.mounted = false; for (const effect of this.effects) effect?.cleanup?.(); }
}
const settle = () => new Promise<void>(resolve => setImmediate(resolve));
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom',
    plugins: [{ name: 'generation-hook-host', enforce: 'pre',
      transform(source, id) {
        if (/\/src\/(?:GenerationStatus\.tsx|Scheme\.tsx|RevisionHistory\.tsx|generationBatch\.ts)$/.test(id)) {
          return source.replace("from 'react'", "from 'virtual:generation-hooks'");
        }
      },
      resolveId(id, importer) {
        if (id === 'virtual:generation-hooks') return '\0generation-hook-host';
        if (!importer?.match(/\/src\/(?:GenerationStatus\.tsx|Scheme\.tsx|RevisionHistory\.tsx|generationBatch\.ts)$/)) return;
        if (id === 'react') return '\0generation-hook-host';
        if (id.startsWith('react/jsx')) return '\0generation-jsx-stub';
        if (importer.endsWith('/GenerationStatus.tsx') && (id === './Feedback' || id === './ui')) return '\0generation-ui-stub';
      },
      load(id) {
        if (id === '\0generation-hook-host') return `export const useState=(...args)=>globalThis.__generationRecoveryHooks.useState(...args);export const useEffect=(...args)=>globalThis.__generationRecoveryHooks.useEffect(...args);export const useRef=value=>globalThis.__generationRecoveryHooks.useRef(value);export const useCallback=fn=>fn;export const useId=()=>':test:';`;
        if (id === '\0generation-ui-stub') return 'export const Feedback=()=>null;export const ErrorBox=()=>null;';
        if (id === '\0generation-jsx-stub') return 'export const jsx=(type,props)=>({type,props});export const jsxs=jsx;export const jsxDEV=jsx;export const Fragment=Symbol();';
      },
    }],
  });
  ({ useGenerationRecovery, useFocusedSchemeRecovery } = await server.ssrLoadModule('/src/GenerationStatus.tsx'));
  ({ Scheme } = await server.ssrLoadModule('/src/Scheme.tsx'));
  ({ RevisionHistory } = await server.ssrLoadModule('/src/RevisionHistory.tsx'));
});
after(async () => { await server?.close(); delete (globalThis as any).__generationRecoveryHooks; });
function fixture(initialBrief: any = { state: 'idle' }, initialParameters?: any, initialScheme?: any, focusedVisible?: boolean) {
  const original = globalThis.fetch;
  const responses: Record<string, any> = { brief: initialBrief, knowledge: { state: 'idle' }, qa: { state: 'idle' }, scheme: initialScheme ?? { state: 'idle' }, parameters: initialParameters ?? { state: 'idle' }, focused_scheme: { state: 'idle' } };
  const calls: { kind: string; method: string }[] = [];
  globalThis.fetch = async (input, options) => {
    const kind = new URL(String(input), 'http://test.local').searchParams.get('kind')!;
    calls.push({ kind, method: options?.method ?? 'GET' });
    const response = responses[kind];
    return response instanceof Response ? response.clone() : Response.json(response);
  };
  const host = new HookHost(); host.includeParameters = initialParameters !== undefined;
  host.focusedVisible = focusedVisible; host.render();
  return { host, responses, calls, dispose() { host.dispose(); globalThis.fetch = original; } };
}
test('an unavailable status keeps only its own view locked and polls it until recovery', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const run = fixture({ state: 'idle' },
    Response.json({ detail: 'Temporary status unavailable' }, { status: 503 }));
  try {
    await settle();
    assert.deepEqual(run.host.value.checkingByKind, {
      brief: false, qa: false, scheme: false, knowledge: false, parameters: true,
    });
    assert.match(run.host.value.error, /Temporary status unavailable/);

    run.responses.brief = { state: 'completed', started_at: 'new-brief' };
    run.host.value.recheck('brief'); await settle();
    assert.equal(run.host.value.version.brief, 1);
    assert.equal(run.host.value.checkingByKind.parameters, true);

    const callsBeforeRetry = run.calls.length;
    run.responses.parameters = { state: 'idle' };
    t.mock.timers.tick(1500); await settle();
    assert.deepEqual(run.calls.slice(callsBeforeRetry), [{ kind: 'parameters', method: 'GET' }]);
    assert.equal(run.host.value.checking, false);
    assert.equal(run.host.value.error, '');
  } finally { run.dispose(); }
});

test('focused status rechecks on return and reloads a run cancelled while the tab switches', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const run = fixture(undefined, undefined, undefined, true);
  try {
    await settle();
    run.host.focusedVisible = false; run.host.render(); await settle();
    run.responses.focused_scheme = { state: 'running', started_at: 'external-run' };
    run.host.focusedVisible = true; run.host.render(); await settle();
    assert.equal(run.host.value.running, true);

    run.responses.focused_scheme = { state: 'cancelled', started_at: 'external-run' };
    run.host.focusedVisible = false; run.host.render(); await settle();
    assert.equal(run.host.value.running, false);
    assert.equal(run.host.value.progress.state, 'cancelled');
    assert.equal(run.host.value.version, 1);
    assert.ok(run.calls.every(call => call.kind === 'focused_scheme' && call.method === 'GET'));
  } finally { run.dispose(); }
});
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

test('switching to Focused Scheme preserves the pending normal revision view', async () => {
  function find(tree: any, predicate: (node: any) => boolean): any {
    if (!tree) return undefined;
    if (Array.isArray(tree)) return tree.map(node => find(node, predicate)).find(Boolean);
    return predicate(tree) ? tree : find(tree.props?.children, predicate);
  }
  const original = globalThis.fetch;
  const host = new HookHost();
  globalThis.fetch = async () => Response.json({
    snapshot: null, inspection: { tree: [], coverage: {} }, freshness: { state: 'unknown' },
  });
  host.renderView = () => Scheme({
    base: '/projects/1', generation: {}, visible: true, onGenerating() {},
    recoveryVersion: 0, recoveryBusy: false, focus: 'balanced', queuedFocus: 'balanced', onFocusChange() {},
  });
  const findButton = (label: string) => find(host.value, node => node.type === 'button' && node.props.children === label);
  try {
    host.render(); await settle();
    const revision = find(host.value, node => node.type === RevisionHistory);
    revision.props.onLoadingChange(true); await settle();
    assert.equal(findButton('Generate Scheme').props.disabled, true);

    findButton('Focused Scheme').props.onClick(); await settle();
    const regularView = find(host.value, node => node.props?.className === 'scheme-overview');
    assert.equal(regularView.props.hidden, true);
    find(regularView, node => node.type === RevisionHistory).props.onLoadingChange(false);
    findButton('Architecture').props.onClick(); await settle();
    assert.equal(findButton('Generate Scheme').props.disabled, false);
  } finally { host.dispose(); globalThis.fetch = original; }
});

test('revision actions retain the timestamp of the selected saved result', async () => {
  function find(tree: any, predicate: (node: any) => boolean): any {
    if (!tree) return undefined;
    if (Array.isArray(tree)) return tree.map(node => find(node, predicate)).find(Boolean);
    return predicate(tree) ? tree : find(tree.props?.children, predicate);
  }
  const original = globalThis.fetch;
  const host = new HookHost();
  const stamp = '2026-01-01T12:00:00+01:00';
  const calls: { method: string; url: URL }[] = [];
  let selectedId: number | null = null;
  globalThis.fetch = async (input, options) => {
    const url = new URL(String(input), 'http://test.local');
    calls.push({ method: options?.method ?? 'GET', url });
    return Response.json(url.pathname.endsWith('/revisions') ? {
      active_id: 2, results: [{ id: 1, generated_at: stamp }], next_offset: null,
    } : { kind: 'brief', snapshot: { generated_at: stamp } });
  };
  host.renderView = () => RevisionHistory({
    base: '/projects/1', kind: 'brief', version: '2026-01-02', disabled: false, selectedId,
    onSelect(_: unknown, id: number | null) { selectedId = id; },
    onLoadingChange() {},
  });
  try {
    host.render(); await settle();
    find(host.value, node => node.type === 'details').props.onToggle({ currentTarget: { open: true } });
    await settle();
    find(host.value, node => node.type === 'li').props.children[0].props.onClick();
    await settle();
    assert.equal(selectedId, 1);
    const activate = find(host.value, node => node.type === 'button' && node.props.children === 'Use this version');
    assert.equal(activate.props.disabled, false);
    activate.props.onClick(); await settle();
    assert.equal(selectedId, null);

    assert.deepEqual(calls.filter(call => !call.url.pathname.endsWith('/revisions')).map(({ method, url }) => [
      method, url.pathname, url.searchParams.get('expected_generated_at'),
    ]), [
      ['GET', '/api/projects/1/codex/revisions/1', stamp],
      ['POST', '/api/projects/1/codex/revisions/1/activate', stamp],
    ]);
  } finally { host.dispose(); globalThis.fetch = original; }
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
  const run = fixture();
  try {
    await settle();
    run.responses.brief = { state: 'failed', started_at: 'brief-failed', error: 'Overview failed' };
    run.responses.qa = { state: 'failed', started_at: 'qa-failed', error: 'Questions failed' };
    run.host.value.recheck('brief');
    run.host.value.recheck('qa'); await settle();
    assert.match(run.host.value.error, /Project Overview · Overview failed/);
    assert.match(run.host.value.error, /AI Q&A · Questions failed/);
    run.host.value.clearError('brief'); await settle();
    assert.doesNotMatch(run.host.value.error, /Overview failed/);
    assert.match(run.host.value.error, /Questions failed/);
  } finally { run.dispose(); }
});

test('previous history and Scheme failures stay in their attempt records while a new dependency waits', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const run = fixture({ state: 'failed', started_at: 'history-old', error: 'History summary cited foreign evidence' },
    undefined, { state: 'failed', started_at: 'scheme-old', error: 'Scheme was skipped because Knowledge failed', latest_attempt: { state: 'skipped' } });
  try {
    await settle();
    assert.equal(run.host.value.error, '');
    assert.equal(run.host.value.progress.brief.error, 'History summary cited foreign evidence');
    assert.equal(run.host.value.progress.scheme.latest_attempt.state, 'skipped');
    run.host.value.recheck('scheme'); await settle();
    assert.equal(run.host.value.checkingByKind.scheme, true);
    assert.equal(run.host.value.error, '');

    run.responses.scheme = { state: 'idle', started_at: 'scheme-new', latest_attempt: { state: 'waiting' } };
    t.mock.timers.tick(1500); await settle();
    assert.equal(run.host.value.running.scheme, true);
    assert.equal(run.host.value.error, '');

    run.responses.qa = { state: 'failed', started_at: 'qa-new', error: 'Current Q&A failure' };
    run.host.value.recheck('qa'); await settle();
    assert.equal(run.host.value.error, 'AI Q&A · Current Q&A failure');
    assert.equal(run.host.value.running.scheme, true);
  } finally { run.dispose(); }
});

test('a new bulk request clears replaced failure banners and keeps unrelated errors', async () => {
  const run = fixture({ state: 'idle' },
    Response.json({ detail: 'Temporary Parameters failure' }, { status: 503 }));
  try {
    await settle();
    run.responses.brief = { state: 'failed', started_at: 'brief-failed', error: 'Earlier failed attempt' };
    run.responses.qa = { state: 'failed', started_at: 'qa-failed', error: 'Independent Q&A failure' };
    run.host.value.recheck('brief'); await settle();
    run.host.value.recheck('qa'); await settle();
    assert.match(run.host.value.error, /Earlier failed attempt/);

    run.host.value.clearAttemptErrors(['brief', 'parameters']); await settle();
    assert.doesNotMatch(run.host.value.error, /Earlier failed attempt/);
    assert.equal(run.host.value.progress.brief.error, 'Earlier failed attempt');
    assert.match(run.host.value.error, /Temporary Parameters failure/);
    assert.match(run.host.value.error, /Independent Q&A failure/);
    assert.equal(run.host.value.checkingByKind.parameters, true);
  } finally { run.dispose(); }
});

// Waiting uses legacy idle plus durable metadata; keep recovering until the
// server-owned dependent request starts or terminates.
test('reloaded queued Scheme follows wait to running and completion, then refreshes only Scheme', async t => {
  const run = fixture({ state: 'idle' }, undefined,
    { state: 'idle', stage: 'Waiting for Knowledge', started_at: 'queue-one', latest_attempt: { state: 'waiting' } });
  try {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    await settle();
    assert.equal(run.host.value.checking, false);
    assert.equal(run.host.value.running.scheme, true);
    assert.equal(run.host.value.version.scheme, 0);
    run.responses.scheme = { state: 'running', started_at: 'scheme-one', latest_attempt: { state: 'running' } };
    t.mock.timers.tick(1500); await settle();
    assert.equal(run.host.value.running.scheme, true);
    run.responses.scheme = { state: 'completed', started_at: 'scheme-one', latest_attempt: { state: 'completed' } };
    t.mock.timers.tick(1500); await settle();
    assert.equal(run.host.value.running.scheme, false);
    assert.equal(run.host.value.version.scheme, 1);
    assert.equal(run.host.value.version.knowledge, 0);
    assert.equal(run.host.value.error, '');
    assert.ok(run.calls.every(call => call.method === 'GET'));
  } finally { run.dispose(); }
});

test('queued Scheme stops recovery and retains skipped or interrupted failure details', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const state of ['skipped', 'interrupted']) {
    const run = fixture({ state: 'idle' }, undefined,
      { state: 'idle', started_at: 'queue-one', latest_attempt: { state: 'waiting' } });
    try {
      await settle();
      assert.equal(run.host.value.running.scheme, true);
      run.responses.scheme = { state: 'failed', started_at: 'queue-one', error: `Scheme ${state}`, latest_attempt: { state } };
      t.mock.timers.tick(1500); await settle();
      assert.equal(run.host.value.running.scheme, false);
      assert.equal(run.host.value.version.scheme, 1);
      assert.match(run.host.value.error, new RegExp(`Scheme ${state}`));
      const before = run.calls.length;
      t.mock.timers.tick(1500); await settle();
      assert.equal(run.calls.length, before);
    } finally { run.dispose(); }
  }
});
