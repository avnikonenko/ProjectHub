import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';

let server: ViteDevServer;
let BriefContent: any;
let BriefPreview: any;
before(async () => {
  server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  ({ BriefContent, BriefPreview } = await server.ssrLoadModule('/src/BriefContent.tsx'));
});
after(async () => { await server?.close(); });
const savedBrief = () => ({ generated_at: '2026-11-17T01:00:00Z',
  sections: { overview: [{ text: 'Project purpose.', source_ids: ['one'] }], architecture: [],
    changes: [], open_questions: [], decision_candidates: [] },
  sources: [{ id: 'one', text: 'Evidence.', label: 'Original' }, { id: 'two', text: 'Other evidence.', label: 'Other' }],
  coverage: ['Overview uses selected original evidence.'], diagnostics: { duration_seconds: 7.5, cache_hits: 3 } });

test('overview coverage is one closed disclosure and no longer appears above the result', () => {
  for (const component of [BriefContent, BriefPreview]) {
    const html = renderToStaticMarkup(createElement(component, { brief: savedBrief(), prefix: 'test' }));
    const marker = '<summary>Coverage &amp; technical details</summary>';
    assert.equal(html.split(marker).length - 1, 1);
    assert.match(html, /<details class="coverage-details generation-details">/);
    assert.doesNotMatch(html, /<details[^>]*\bopen(?:=|[ >])/);
    assert.ok(html.indexOf('Overview uses selected original evidence.') > html.indexOf(marker));
    assert.match(html, /Cited sources in this result: 2/);
    assert.match(html, /7\.5 seconds/);
    assert.doesNotMatch(html, /Preview coverage/);
  }
});

test('historical overview keeps its recorded measurements and legacy metadata stays unknown', () => {
  const historical = savedBrief();
  const before = structuredClone(historical);
  const old = renderToStaticMarkup(createElement(BriefContent, { brief: historical, prefix: 'history' }));
  const newer = renderToStaticMarkup(createElement(BriefContent, {
    brief: { ...savedBrief(), diagnostics: { duration_seconds: 99 } }, prefix: 'new' }));
  assert.match(old, /7\.5 seconds/); assert.doesNotMatch(old, /99\.0 seconds/);
  assert.match(newer, /99\.0 seconds/);
  const { diagnostics: _, ...legacy } = historical;
  const html = renderToStaticMarkup(createElement(BriefContent, { brief: legacy, prefix: 'legacy' }));
  assert.match(html, /Exact repository file coverage was not recorded for this result/);
  assert.doesNotMatch(html, /0\.0 seconds/);
  assert.deepEqual(historical, before);
});
