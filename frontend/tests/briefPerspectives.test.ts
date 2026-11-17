import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';

let server: ViteDevServer;
let BriefPreview: any;
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  BriefPreview = (await server.ssrLoadModule('/src/BriefContent.tsx')).BriefPreview;
});
after(async () => { await server?.close(); });

test('Knowledge Summary and Project Overview use distinct labels without rewriting saved content', () => {
  const brief = { generated_at: 'then', sections: {
    overview: [{ text: 'Preserved project statement.', source_ids: [] }], architecture: [],
    changes: [], open_questions: [], decision_candidates: [],
  }, sources: [], coverage: [] };
  const original = JSON.stringify(brief);
  const overview = renderToStaticMarkup(createElement(BriefPreview, { brief, prefix: '1-general' }));
  const knowledge = renderToStaticMarkup(createElement(BriefPreview, { brief, prefix: '1-knowledge',
    perspective: 'knowledge', sourceLabel: 'Knowledge Summary sources' }));
  assert.match(overview, /brief-topic">Overview<\/h3>/);
  assert.match(knowledge, /brief-topic">Summary<\/h3>/);
  assert.doesNotMatch(knowledge, /brief-topic">Overview<\/h3>|Preview sources/);
  assert.match(knowledge, /Knowledge Summary sources/);
  assert.match(overview, /Preserved project statement/);
  assert.match(knowledge, /Preserved project statement/);
  assert.equal(JSON.stringify(brief), original);
});
