import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';

let server: ViteDevServer;
let ProjectBrief: any;
let generateProjectOverview: any;
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  const module = await server.ssrLoadModule('/src/ProjectBrief.tsx');
  ProjectBrief = module.ProjectBrief;
  generateProjectOverview = module.generateProjectOverview;
});
after(async () => { await server?.close(); });

function overview() {
  return { generated_at: '2026-11-17T00:00:00Z', sections: { overview: [], current_status: [] },
    sources: [], coverage: [] };
}

test('general Project Overview has its own title and refresh action', () => {
  const html = renderToStaticMarkup(createElement(ProjectBrief, { base: '/projects/2', visible: true,
    generation: { model: '', effort: '' }, onGenerating() {}, recoveryVersion: 0, recoveryBusy: false }));
  assert.match(html, /Project Overview/);
  assert.match(html, /Refresh Project Overview/);
  assert.doesNotMatch(html, /Refresh project analysis|Overview preview|Knowledge Summary/);
});

test('overview generation posts only to the independent brief endpoint with selected settings', async () => {
  const signal = new AbortController().signal;
  const settings = { model: 'chosen-model', effort: 'medium' };
  const original = structuredClone(settings);
  let calls = 0;
  const response = overview();
  const saved = await generateProjectOverview('/projects/2', settings, signal, async (path: string, options: RequestInit) => {
    calls++;
    assert.equal(path, '/projects/2/codex/brief');
    assert.equal(options.method, 'POST');
    assert.equal(options.signal, signal);
    assert.deepEqual(JSON.parse(String(options.body)), { confirmed: true, model: 'chosen-model', effort: 'medium' });
    return response;
  });
  assert.equal(calls, 1);
  assert.equal(saved, response);
  assert.deepEqual(settings, original);
});

test('default model settings remain null and failures are not retried through Knowledge', async () => {
  let calls = 0;
  await assert.rejects(generateProjectOverview('/projects/2', { model: '', effort: '' },
    new AbortController().signal, async (path: string, options: RequestInit) => {
      calls++;
      assert.equal(path, '/projects/2/codex/brief');
      assert.deepEqual(JSON.parse(String(options.body)), { confirmed: true, model: null, effort: null });
      throw new Error('Codex unavailable');
    }), /Codex unavailable/);
  assert.equal(calls, 1);
});

test('foreign paths, already-aborted operations and incomplete responses cannot become an overview', async () => {
  let calls = 0;
  const request = async () => { calls++; return overview(); };
  for (const base of ['/projects/0', '/projects/2/../3', '/projects/2/codex/analysis', '/other/2'])
    await assert.rejects(generateProjectOverview(base, { model: '', effort: '' }, new AbortController().signal, request));
  const abort = new AbortController(); abort.abort();
  await assert.rejects(generateProjectOverview('/projects/2', { model: '', effort: '' }, abort.signal, request));
  assert.equal(calls, 0);
  for (const response of [null, {}, { ...overview(), sections: {} }, { ...overview(), coverage: 'invalid' },
    { ...overview(), sections: { overview: [{ text: 'Invalid citation', source_ids: [1] }] } }])
    await assert.rejects(generateProjectOverview('/projects/2', { model: '', effort: '' },
      new AbortController().signal, async () => response), /incomplete/);
});
