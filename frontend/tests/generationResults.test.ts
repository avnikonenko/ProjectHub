import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';
import { projectGenerationKinds } from '../src/generationBatch.ts';

let server: ViteDevServer;
let parseGenerationResults: any, readGenerationResults: any, GenerationResultRows: any,
  initializeGenerationSelection: any, selectableGenerationKinds: any;
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  ({ parseGenerationResults, readGenerationResults, GenerationResultRows, initializeGenerationSelection,
    selectableGenerationKinds } = await server.ssrLoadModule('/src/GenerationResults.tsx'));
});

test('unfinished defaults initialize once and polling never overwrites the user selection', () => {
  const raw = fixture();
  Object.assign(raw.views[3], { state: 'timed_out', retryable: true });
  const initial = initializeGenerationSelection(null, parseGenerationResults(raw, 2));
  assert.deepEqual(initial, { project_id: 2, selected: ['qa'] });
  const userCleared = { project_id: 2, selected: [] };
  assert.equal(initializeGenerationSelection(userCleared, parseGenerationResults(raw, 2)), userCleared);
  const other = { ...raw, project_id: 3 };
  assert.deepEqual(initializeGenerationSelection(userCleared, parseGenerationResults(other, 3)), { project_id: 3, selected: ['qa'] });
});

test('checkboxes expose accessible labels and busy or waiting views cannot be selected', () => {
  const raw = fixture();
  Object.assign(raw.views[1], { state: 'running', retryable: false });
  Object.assign(raw.views[2], { state: 'waiting', retryable: false });
  const data = parseGenerationResults(raw, 2);
  assert.deepEqual(selectableGenerationKinds(data, { qa: true }), ['brief', 'parameters']);
  const html = renderToStaticMarkup(createElement(GenerationResultRows, {
    data, selected: ['brief'], busy: { qa: true }, onToggle: () => {},
  }));
  const inputs = [...html.matchAll(/<input[^>]+>/g)].map(match => match[0]);
  assert.equal(inputs.length, 5);
  assert.match(inputs[0], /Select Overview for regeneration/);
  assert.match(inputs[0], /checked/);
  assert.doesNotMatch(inputs[0], /disabled/);
  for (const index of [1, 2, 3]) assert.match(inputs[index], /disabled/);
  assert.doesNotMatch(inputs[4], /disabled/);
});

test('summary loading uses only a read and rejects aborted, malformed or foreign responses', async () => {
  let calls = 0;
  const controller = new AbortController();
  const request = async (path: string, options: RequestInit) => {
    calls++; assert.equal(path, '/projects/2/codex/generation/summary');
    assert.equal(options.signal, controller.signal); assert.equal(options.method, undefined);
    return fixture();
  };
  await readGenerationResults('/projects/2', controller.signal, request);
  assert.equal(calls, 1);
  await assert.rejects(readGenerationResults('/projects/2/../3', controller.signal, request));
  assert.equal(calls, 1);
  controller.abort();
  await assert.rejects(readGenerationResults('/projects/2', controller.signal, request));
  assert.equal(calls, 1);
  await assert.rejects(readGenerationResults('/projects/2', new AbortController().signal,
    async () => ({ ...fixture(), project_id: 3 })), /another project/);
  const stale = new AbortController();
  await assert.rejects(readGenerationResults('/projects/2', stale.signal, async () => {
    stale.abort(); return fixture();
  }));
});
after(async () => { await server?.close(); });
function fixture() {
  return { project_id: 2, views: projectGenerationKinds.map(kind => ({ kind, state: 'saved',
    saved_at: '2026-11-17T12:00:00Z', showing_previous: false, retryable: false,
    latest_attempt: { state: 'completed', started_at: '2026-11-17T11:58:00Z', finished_at: '2026-11-17T12:00:00Z', error: null } })) };
}

test('summary requires the intended project and every independent view exactly once', () => {
  const raw = fixture();
  assert.deepEqual(parseGenerationResults(raw, 2).views.map((view: any) => view.kind), projectGenerationKinds);
  assert.throws(() => parseGenerationResults(raw, 3), /another project/);
  assert.throws(() => parseGenerationResults({ ...raw, views: raw.views.slice(1) }, 2), /incomplete/);
  assert.throws(() => parseGenerationResults({ ...raw, views: [...raw.views.slice(1), raw.views[1]] }, 2), /duplicate/);
  assert.throws(() => parseGenerationResults({ ...raw, views: raw.views.map((view, index) => index ? view : { ...view, state: 'invented' }) }, 2), /invalid/);
  assert.throws(() => parseGenerationResults({ ...raw, views: raw.views.map((view, index) => index ? view : {
    ...view, latest_attempt: { ...view.latest_attempt, project_id: 3 },
  }) }, 2), /attempt is invalid/);
  assert.throws(() => parseGenerationResults({ ...raw, views: raw.views.map((view, index) => index ? view : {
    ...view, latest_attempt: { ...view.latest_attempt, kind: 'knowledge' },
  }) }, 2), /attempt is invalid/);
});

test('a timed-out attempt remains visible alongside the earlier saved result and escaped error', () => {
  const raw = fixture();
  Object.assign(raw.views[0], { state: 'timed_out', showing_previous: true, retryable: true,
    latest_attempt: { state: 'failed', started_at: '2026-11-17T12:10:00Z', finished_at: '2026-11-17T12:25:00Z', error: '<script>timeout</script>' } });
  const html = renderToStaticMarkup(createElement(GenerationResultRows, { data: parseGenerationResults(raw, 2) }));
  assert.match(html, /Timed out/);
  assert.match(html, /Showing the earlier saved result/);
  assert.match(html, /Attempt details/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;timeout/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
});

test('waiting, interrupted and missing results are distinct from an API failure or successful save', () => {
  const raw = fixture();
  Object.assign(raw.views[1], { state: 'interrupted', saved_at: null, retryable: true });
  Object.assign(raw.views[2], { state: 'waiting', saved_at: null, retryable: false });
  Object.assign(raw.views[3], { state: 'not_generated', saved_at: null, latest_attempt: null, retryable: true });
  const html = renderToStaticMarkup(createElement(GenerationResultRows, { data: parseGenerationResults(raw, 2) }));
  assert.match(html, /Interrupted/);
  assert.match(html, /Waiting for Knowledge/);
  assert.match(html, /Not generated/);
  assert.match(html, /No saved result yet/);
  assert.throws(() => parseGenerationResults({ detail: 'status unavailable' }, 2), /incomplete/);
});

test('an earlier Knowledge result stays accessible without claiming it is current', () => {
  const raw = fixture();
  Object.assign(raw.views[1], { state: 'earlier_result', saved_at: null,
    history_at: '2026-11-17T12:15:00Z', latest_attempt: null, retryable: true });
  const data = parseGenerationResults(raw, 2);
  assert.equal(data.views[1].saved_at, null);
  assert.equal(data.views[1].history_at, '2026-11-17T12:15:00Z');
  assert.deepEqual(initializeGenerationSelection(null, data).selected, ['knowledge']);
  assert(selectableGenerationKinds(data).includes('knowledge'));
  const html = renderToStaticMarkup(createElement(GenerationResultRows, { data, onToggle: () => {} }));
  const knowledge = html.split('<li>').find(part => part.includes('Select Knowledge')) ?? '';
  assert.match(knowledge, /<strong>Earlier result available<\/strong>/);
  assert.match(knowledge, /Earlier saved version ·/);
  assert.doesNotMatch(knowledge, /No saved result yet|<small>Saved|disabled|[Pp]ublished|[Pp]review/);
});

test('failed generation retains its failure alongside an earlier saved result', () => {
  const raw = fixture();
  Object.assign(raw.views[1], { state: 'failed', saved_at: null,
    history_at: '2026-11-17T12:15:00Z', showing_previous: false, retryable: true,
    latest_attempt: { state: 'failed', started_at: '2026-11-17T12:20:00Z',
      finished_at: '2026-11-17T12:25:00Z', error: 'Generation unavailable.' } });
  const data = parseGenerationResults(raw, 2);
  const html = renderToStaticMarkup(createElement(GenerationResultRows, { data }));
  assert.match(html, /<strong>Failed<\/strong>/);
  assert.match(html, /Earlier saved version ·/);
  assert.match(html, /Generation unavailable/);
  assert.deepEqual(initializeGenerationSelection(null, data).selected, ['knowledge']);
});

test('a current saved result takes display precedence over older history', () => {
  const raw = fixture();
  Object.assign(raw.views[1], { history_at: '2026-11-17T11:00:00Z' });
  const html = renderToStaticMarkup(createElement(GenerationResultRows, { data: parseGenerationResults(raw, 2) }));
  assert.doesNotMatch(html, /Earlier saved version|Earlier result available/);
  assert.equal((html.match(/<strong>Saved<\/strong>/g) ?? []).length, 5);
});

test('old summary responses stay compatible while invalid or misplaced history is rejected', () => {
  assert.equal(parseGenerationResults(fixture(), 2).views[1].history_at, null);
  for (const history_at of [false, 42, {}, '', 'not a date', '<script>bad</script>']) {
    const raw = fixture();
    Object.assign(raw.views[1], { history_at });
    assert.throws(() => parseGenerationResults(raw, 2), /earlier generation result is invalid/);
  }
  const otherKind = fixture();
  Object.assign(otherKind.views[0], { history_at: '2026-11-17T12:15:00Z' });
  assert.throws(() => parseGenerationResults(otherKind, 2), /earlier generation result is invalid/);
  for (const changes of [{ saved_at: null }, { history_at: '2026-11-17T12:15:00Z' },
    { saved_at: null, history_at: '2026-11-17T12:15:00Z', showing_previous: true }]) {
    const raw = fixture();
    Object.assign(raw.views[1], { state: 'earlier_result', ...changes });
    assert.throws(() => parseGenerationResults(raw, 2), /earlier generation result is invalid/);
  }
});
