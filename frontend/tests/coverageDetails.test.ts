import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';
let server: ViteDevServer;
let CoverageDetails: any;
before(async () => {
  server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true, include: [] } });
  CoverageDetails = (await server.ssrLoadModule('/src/CoverageDetails.tsx')).CoverageDetails;
});
after(async () => { await server?.close(); });
test('coverage is one closed disclosure with saved run measurements', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, {
    savedAt: '2026-11-17T10:00:00Z', sourceCount: 4, coverage: ['One linked session was unavailable.'],
    diagnostics: { duration_seconds: 12.5, input_records: 30, input_chars: 25000, archive_cache_hits: 3 },
  }));
  assert.equal((html.match(/<details\b/g) ?? []).length, 1);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
  assert.match(html, /Coverage &amp; technical details/);
  assert.match(html, /12\.5 seconds/);
  assert.match(html, /Input records/); assert.match(html, /25,000/);
  assert.match(html, /One linked session was unavailable/);
  assert.match(html, /Cited sources in this result: 4/);
  assert.doesNotMatch(html, /<summary>Generation details/);
});
test('old results do not invent zero processing coverage', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, { sourceCount: 2 }));
  assert.match(html, /not recorded for this saved result/);
  assert.doesNotMatch(html, /0%|0 of|Input records|Fresh extraction requests/);
});
test('malformed metadata is ignored and coverage strings remain escaped data', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, {
    savedAt: 'invalid date', sourceCount: -1, coverage: { limitations: ['<script>inert</script>', 5], processed: null },
    diagnostics: { duration_seconds: -1, input_records: -2, input_chars: Infinity },
  }));
  assert.match(html, /&lt;script&gt;inert&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>|Invalid Date|Cited sources|Infinity/);
});
test('view-specific measurements use the same closed outer disclosure', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, { note: 'Current inspection, separate from the saved result.' },
    createElement('p', null, 'Files read: 12 of 15 eligible files.')));
  assert.equal((html.match(/<details\b/g) ?? []).length, 1);
  assert.match(html, /Files read: 12 of 15/);
  assert.doesNotMatch(html, /not recorded for this saved result/);
});
