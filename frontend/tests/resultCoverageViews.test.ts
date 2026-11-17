import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';

let server: ViteDevServer;
let BriefContent: any;
let BriefCoverage: any;
let BriefPreview: any;
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  ({ BriefContent, BriefCoverage, BriefPreview } = await server.ssrLoadModule('/src/BriefContent.tsx'));
});
after(async () => { await server?.close(); });
const savedBrief = () => ({ generated_at: '2026-11-17T01:00:00Z',
  sections: { overview: [{ text: 'Project purpose.', source_ids: ['one'] }], architecture: [],
    changes: [], open_questions: [], decision_candidates: [] },
  sources: [{ id: 'one', text: 'Evidence.', label: 'Original' }, { id: 'two', text: 'Other evidence.', label: 'Other' }],
  coverage: ['Overview uses selected original evidence.'], diagnostics: { duration_seconds: 7.5, cache_hits: 3 } });

test('the brief narrative stays separate from coverage and sources in the support card', () => {
  const props = { brief: savedBrief(), prefix: 'test' };
  const narrative = renderToStaticMarkup(createElement(BriefContent, props));
  assert.match(narrative, /Project purpose\./);
  assert.doesNotMatch(narrative, /<summary>Coverage<\/summary>|Preview sources/);

  const html = renderToStaticMarkup(createElement(BriefPreview, props));
  const marker = '<summary>Coverage</summary>';
  assert.equal(html.split(marker).length - 1, 1);
  assert.match(html, /class="card result-support"><details class="coverage-details generation-details">/);
  assert.doesNotMatch(html, /<details[^>]*\bopen(?:=|[ >])/);
  assert.ok(html.indexOf('Project purpose.') < html.indexOf('card result-support'));
  assert.ok(html.indexOf('Overview uses selected original evidence.') > html.indexOf(marker));
  assert.match(html, /<li>1 cited source<\/li>/);
  assert.match(html, /Cited sources in this result: 1/);
  assert.match(html, /7\.5 seconds/);
  assert.match(html, /Preview sources \(1\)/);
  assert.match(html, /id="test-source-1"/);
});

test('historical overview keeps its recorded measurements and legacy metadata stays unknown', () => {
  const historical = savedBrief();
  const before = structuredClone(historical);
  const old = renderToStaticMarkup(createElement(BriefCoverage, { brief: historical, sourceCount: 1 }));
  const newer = renderToStaticMarkup(createElement(BriefCoverage, {
    brief: { ...savedBrief(), diagnostics: { duration_seconds: 99 } }, sourceCount: 1 }));
  assert.match(old, /7\.5 seconds/); assert.doesNotMatch(old, /99\.0 seconds/);
  assert.match(newer, /99\.0 seconds/);
  const { diagnostics: _, ...legacy } = historical;
  const html = renderToStaticMarkup(createElement(BriefCoverage, { brief: legacy, sourceCount: 1 }));
  assert.match(html, /Exact repository file coverage was not recorded for this result/);
  assert.doesNotMatch(html, /0\.0 seconds/);
  assert.deepEqual(historical, before);
});
