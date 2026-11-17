import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';

let server: ViteDevServer;
let GenerationDetails: any;
before(async () => {
  server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  GenerationDetails = (await server.ssrLoadModule('/src/GenerationDetails.tsx')).GenerationDetails;
});
after(async () => { await server?.close(); });

test('generation details expose optional parallel timings without cluttering old results', () => {
  const timing = { level: 0, batch: 1, input_chars: 140000, seconds: 15.75,
    model_seconds: 15, cache_lookup_seconds: 0.05, local_seconds: 0.7, cached: false, completed: true };
  const html = renderToStaticMarkup(createElement(GenerationDetails, { diagnostics: {
    overview_selection: { synthesis_workers: 4, synthesis_batch_timings: [timing,
      { ...timing, batch: 2, completed: false }] },
  } }));
  assert.match(html, /Parallel synthesis workers/);
  assert.match(html, /<summary>Synthesis batch timings<\/summary>/);
  assert.match(html, /Batch times can overlap/);
  assert.match(html, /15\.75/); assert.match(html, /Incomplete/);
  const legacy = renderToStaticMarkup(createElement(GenerationDetails, { diagnostics: { duration_seconds: 5 } }));
  assert.doesNotMatch(legacy, /Synthesis batch timings|Parallel synthesis workers/);
});

test('timing disclosure bounds visible rows and discloses omissions', () => {
  const timings = Array.from({ length: 101 }, (_, index) => ({ level: 0, batch: index + 1,
    input_chars: 10, seconds: 1, model_seconds: 1, cache_lookup_seconds: 0, local_seconds: 0,
    cached: true, completed: true }));
  const html = renderToStaticMarkup(createElement(GenerationDetails, { diagnostics: {
    overview_selection: { synthesis_batch_timings: timings, synthesis_batch_timings_truncated: true },
  } }));
  assert.equal((html.match(/<tr>/g) || []).length, 101); // One header plus 100 data rows.
  assert.match(html, /first 100 of/);
  assert.match(html, /Some timing records were omitted/);
});
