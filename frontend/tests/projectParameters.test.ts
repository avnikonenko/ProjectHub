import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';
import { filterParameters, parameterDefault, parameterGroups, parseParameterResult, parseParameterSnapshot,
  parameterRevisionResult,
  type ParameterSnapshot, type ProjectParameter } from '../src/parameters.ts';

let server: ViteDevServer;
let ProjectParameters: any, ParameterList: any, ParameterCoverage: any, parameterFreshnessLabel: any,
  generateProjectParameters: any, recoverProjectParameters: any;
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  ({ ProjectParameters, ParameterList, ParameterCoverage, parameterFreshnessLabel, generateProjectParameters, recoverProjectParameters } =
    await server.ssrLoadModule('/src/ProjectParameters.tsx'));
});
after(async () => { await server?.close(); });

function item(overrides: Partial<ProjectParameter> = {}): ProjectParameter {
  return { id: 'parameter:one', name: 'MAX_CONTEXT', component: 'analysis', path: 'app/config.py',
    symbol: 'MAX_CONTEXT', line_start: 12, line_end: 12, default_value: '40000', default_kind: 'literal',
    source_ids: ['source:one'], purpose: 'Bound selected evidence.', purpose_basis: 'documented',
    rationale: '', rationale_basis: 'unknown', trade_off: 'Some relevant material may be omitted.',
    trade_off_basis: 'inferred', units: 'characters', explanation_source_ids: ['source:one'], ...overrides };
}
function snapshot(overrides: Partial<ParameterSnapshot> = {}): ParameterSnapshot {
  return { project_id: 2, generated_at: '2026-11-17T00:00:00Z', fingerprint: 'one',
    items: [item()], sources: [{ id: 'source:one', label: 'Context budget declaration',
      text: 'MAX_CONTEXT = 40000', path: 'app/config.py', revision: 'revision-one', line_start: 12, line_end: 12 }],
    coverage: { partial: true, limitations: ['Bounded repository inspection.'] }, ...overrides };
}
const result = (saved: ParameterSnapshot | null = snapshot()) => ({ snapshot: saved,
  freshness: { state: 'unchanged', partial: true, limitations: ['Bounded repository inspection.'] } });

test('parameter view is independent, explicit and does not invent runtime values', () => {
  const html = renderToStaticMarkup(createElement(ProjectParameters, { base: '/projects/2', visible: true,
    generation: { model: '', effort: '' } }));
  assert.match(html, /Parameters &amp; Trade-offs/);
  assert.match(html, /Generate parameters/);
  assert.match(html, /not values verified in a particular run/);
  assert.doesNotMatch(html, /Refresh Project Overview|Save Knowledge Summary|Generate Scheme/);
});

test('literal, required, unspecified and dynamic defaults retain their actual meanings', () => {
  assert.equal(parameterDefault(item()), '40000');
  assert.equal(parameterDefault(item({ default_value: null, default_kind: 'required' })), 'Required — no declared default');
  assert.equal(parameterDefault(item({ default_value: null, default_kind: 'unspecified' })), 'No declared default');
  const expression = item({ default_value: 'min(12, os.cpu_count())', default_kind: 'expression' });
  assert.equal(parameterDefault(expression), 'min(12, os.cpu_count())');
  const html = renderToStaticMarkup(createElement(ParameterList, { snapshot: snapshot({ items: [expression] }), prefix: '2-parameters' }));
  assert.match(html, /expression; not evaluated/);
  assert.match(html, /min\(12, os.cpu_count\(\)\)/);
  assert.match(html, /AI-inferred/);
  assert.match(html, /Not documented in the inspected evidence/);
  assert.match(html, /revision-one/);
  const configured = renderToStaticMarkup(createElement(ParameterList, {
    snapshot: snapshot({ items: [item({ declaration_kind: 'configuration_value', default_value: '8' })] }), prefix: '2-config',
  }));
  assert.match(configured, /Declared configuration value/);
  assert.match(configured, /Declared value/);
  assert.doesNotMatch(configured, /Declared default/);
});

test('search includes values, rationale and exact evidence while component filtering stays exact', () => {
  const saved = snapshot({ items: [item(), item({ id: 'parameter:two', component: 'analysis workers', default_value: '10' })] });
  const original = JSON.stringify(saved);
  assert.equal(filterParameters(saved.items, 'context budget declaration', '', saved.sources).length, 2);
  assert.equal(filterParameters(saved.items, '  OMITTED  ', 'analysis', saved.sources).length, 1);
  assert.equal(filterParameters(saved.items, '10', 'analysis', saved.sources).length, 0);
  assert.deepEqual(parameterGroups(saved.items).map(([name]) => name), ['analysis', 'analysis workers']);
  assert.equal(JSON.stringify(saved), original);
});

test('conditional declarations retain both values and conditions as separate code facts', () => {
  const saved = snapshot({ items: [
    item({ id: 'workers:default', name: 'MAX_WORKERS', default_value: '10', condition: 'not (sys.platform == "win32")' }),
    item({ id: 'workers:windows', name: 'MAX_WORKERS', default_value: '2', line_start: 14, line_end: 14,
      condition: 'sys.platform == "win32"' }),
  ] });
  const original = JSON.stringify(saved);
  const parsed = parseParameterSnapshot(saved, 2);
  assert.equal(parsed.items.length, 2);
  assert.deepEqual(parsed.items.map(value => value.default_value), ['10', '2']);
  assert.deepEqual(parsed.items.map(value => value.condition), saved.items.map(value => value.condition));
  const html = renderToStaticMarkup(createElement(ParameterList, { snapshot: parsed, prefix: '2-parameters' }));
  assert.equal((html.match(/conditional declaration/g) ?? []).length, 2);
  assert.equal((html.match(/Declaration condition/g) ?? []).length, 2);
  assert.match(html, /sys.platform == &quot;win32&quot;/);
  assert.match(html, /not evaluated/);
  assert.equal(filterParameters(parsed.items, 'win32').length, 2);
  assert.equal(JSON.stringify(saved), original);
  assert.throws(() => parseParameterSnapshot(snapshot({ items: [item({ condition: {} as any })] }), 2));
});

test('API validation protects project scope, declaration evidence and safe metadata', () => {
  const saved = snapshot();
  assert.equal(parseParameterSnapshot(saved, 2).items[0].default_value, '40000');
  const withNavigation = { ...saved, sources: [{ ...saved.sources[0], turn_id: { bad: true }, session_id: 9, note_id: 8 }] };
  const parsed = parseParameterSnapshot(withNavigation, 2);
  assert.equal(parsed.sources[0].session_id, undefined);
  assert.equal(parsed.sources[0].note_id, undefined);
  for (const raw of [null, { ...saved, project_id: 3 },
    { ...saved, items: [item({ source_ids: ['foreign'] })] },
    { ...saved, items: [item(), item()] }, { ...saved, items: [item({ default_kind: 'required', default_value: 'false' })] },
    { ...saved, sources: [{ ...saved.sources[0], revision: {} }] },
    { ...saved, generation: { account: {}, model: 'x', effort: 'medium' } },
    { ...saved, items: [item({ rationale_basis: 'inferred' as any })] }])
    assert.throws(() => parseParameterSnapshot(raw, 2));
  assert.throws(() => parseParameterResult({ snapshot: saved, freshness: {} }, 2));
});

test('source snippets and inferred explanations render as escaped data', () => {
  const saved = snapshot({ items: [item({ name: '<script>bad()</script>', trade_off: '<img onerror=bad()>' })],
    sources: [{ ...snapshot().sources[0], text: '<script>source()</script>' }] });
  const html = renderToStaticMarkup(createElement(ParameterList, { snapshot: saved, prefix: '2-parameters' }));
  assert.doesNotMatch(html, /<script>|<img onerror/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /AI-inferred/);
});

test('unknown current source checks stay distinct from saved coverage and historical revisions', () => {
  const freshness = { state: 'unknown', partial: true, limitations: ['Current repository unavailable.'] };
  assert.equal(parameterFreshnessLabel(freshness), 'Source freshness is unknown.');
  assert.equal(parameterFreshnessLabel(freshness, true), undefined);
  const current = renderToStaticMarkup(createElement(ParameterCoverage, { snapshot: snapshot(), freshness }));
  assert.match(current, /Bounded repository inspection/);
  assert.match(current, /Current source check/);
  assert.match(current, /Current repository unavailable/);
  assert.match(current, /separately from the inspection recorded with the saved result/);
  assert.doesNotMatch(current, /<details[^>]*\bopen\b/);
  const old = renderToStaticMarkup(createElement(ParameterCoverage, { snapshot: snapshot(), freshness, historical: true }));
  assert.match(old, /Bounded repository inspection/);
  assert.doesNotMatch(old, /Current source check|Current repository unavailable/);
});

test('returning from a historical revision never reuses an earlier unchanged-source verdict', () => {
  const previous = result();
  const historical = snapshot({ generated_at: '2026-11-16T00:00:00Z', fingerprint: 'older' });
  const selected = parameterRevisionResult(previous, historical, true);
  assert.equal(selected.snapshot?.fingerprint, 'older');
  const latest = parameterRevisionResult(selected, snapshot(), false);
  assert.equal(latest.snapshot?.fingerprint, 'one');
  assert.equal(parameterFreshnessLabel(latest.freshness), 'Source freshness is unknown.');
  assert.notEqual(latest.freshness.state, previous.freshness.state);
  assert.equal(previous.freshness.state, 'unchanged');
});

test('generation makes one parameters POST with selected settings and no alternative pipeline', async () => {
  let calls = 0;
  const signal = new AbortController().signal;
  await generateProjectParameters('/projects/2', { model: 'chosen', effort: 'medium' }, signal,
    async (path: string, options: RequestInit) => {
      calls++; assert.equal(path, '/projects/2/codex/parameters'); assert.equal(options.method, 'POST');
      assert.equal(options.signal, signal);
      assert.deepEqual(JSON.parse(String(options.body)), { confirmed: true, model: 'chosen', effort: 'medium' });
      return snapshot();
    });
  assert.equal(calls, 1);
  await assert.rejects(generateProjectParameters('/projects/2', { model: '', effort: '' }, signal, async () => {
    calls++; throw new Error('Generation failed');
  }), /Generation failed/);
  assert.equal(calls, 2);
  await assert.rejects(generateProjectParameters('/projects/2/../3', { model: '', effort: '' }, signal));
});

test('read-only recovery preserves the old saved result while running and reloads completion', async () => {
  const events: any[] = []; const paths: string[] = []; let statuses = 0, savedReads = 0;
  await recoverProjectParameters('/projects/2', 2, new AbortController().signal,
    (status: any, current: any) => events.push({ status, current }),
    async (path: string, options: RequestInit) => {
      paths.push(path); assert.notEqual(options.method, 'POST');
      if (path.includes('generation/status')) return ++statuses === 1
        ? { state: 'running', started_at: 'new' } : { state: 'completed', started_at: 'new' };
      return result(snapshot({ generated_at: ++savedReads === 1 ? 'old' : 'new' }));
    }, async () => {});
  assert.equal(events[0].status.state, 'running'); assert.equal(events[0].current.snapshot.generated_at, 'old');
  assert.equal(events.at(-1).current.snapshot.generated_at, 'new');
  assert.equal(paths.filter(path => !path.includes('generation/status')).length, 2);
});

test('ordinary browsing uses reads only and lost-response handoff tolerates a previous terminal status', async () => {
  let statuses = 0, waits = 0; const events: any[] = [];
  await recoverProjectParameters('/projects/2', 2, new AbortController().signal,
    (status: any, current: any) => events.push({ status, current }),
    async (path: string, options: RequestInit) => {
      assert.notEqual(options.method, 'POST');
      if (!path.includes('generation/status')) return result();
      statuses++;
      return statuses === 1 ? { state: 'completed', started_at: 'previous' }
        : statuses === 2 ? { state: 'running', started_at: 'new' } : { state: 'completed', started_at: 'new' };
    }, async () => { waits++; }, { previousStartedAt: 'previous' });
  assert.equal(waits, 2); assert.equal(statuses, 3);
  assert.equal(events[0].status.started_at, 'new');
  let calls = 0;
  await recoverProjectParameters('/projects/2', 2, new AbortController().signal, () => {}, async (path: string) => {
    calls++; return path.includes('generation/status') ? { state: 'idle' } : result(null);
  }, async () => { throw new Error('Idle browsing must not poll'); });
  assert.equal(calls, 2);
});

test('abort and foreign paths never start recovery or generation', async () => {
  const abort = new AbortController(); abort.abort(); let calls = 0;
  const request = async () => { calls++; return result(); };
  await assert.rejects(recoverProjectParameters('/projects/2', 2, abort.signal, () => {}, request));
  await assert.rejects(recoverProjectParameters('/projects/3', 2, new AbortController().signal, () => {}, request));
  await assert.rejects(generateProjectParameters('/projects/2', { model: '', effort: '' }, abort.signal, request));
  assert.equal(calls, 0);
});
