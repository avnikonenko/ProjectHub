import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';
let server: ViteDevServer;
let SchemeKnowledgeContext: any;
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  SchemeKnowledgeContext = (await server.ssrLoadModule('/src/SchemeKnowledgeContext.tsx')).SchemeKnowledgeContext;
});
after(async () => { await server?.close(); });

test('saved Knowledge metadata uses version terminology inside a closed disclosure', () => {
  const html = renderToStaticMarkup(createElement(SchemeKnowledgeContext, { context: {
    status: 'published', publication_id: 'published-version-one', available_items: 69,
    included_items: 8, omitted_items: 61,
  } }));
  assert.match(html, /^<details[^>]*><summary>Saved Knowledge context<\/summary>/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
  assert.match(html, /Knowledge version<\/dt><dd>published-version-one/);
  assert.match(html, /Items available in this version<\/dt><dd>69/);
  assert.match(html, /Items included in Scheme evidence<\/dt><dd>8/);
  assert.match(html, /Items omitted from Scheme evidence<\/dt><dd>61/);
  assert.doesNotMatch(html, /Published Knowledge context|unpublished previews/);
  assert.match(html, /Browsing saved details makes no AI requests/);
});

test('missing, absent and unavailable contexts do not invent a publication', () => {
  const missing = renderToStaticMarkup(createElement(SchemeKnowledgeContext));
  assert.match(missing, /not recorded for this saved Scheme/);
  assert.doesNotMatch(missing, /Items available in this version|<dd>0<\/dd>/);
  const absent = renderToStaticMarkup(createElement(SchemeKnowledgeContext, { context: { status: 'absent' } }));
  assert.match(absent, /No saved Knowledge was available when this Scheme was generated/);
  const unavailable = renderToStaticMarkup(createElement(SchemeKnowledgeContext, { context: { status: 'unavailable' } }));
  assert.match(unavailable, /could not be read for this Scheme/);
  assert.doesNotMatch(unavailable, /Knowledge version<\/dt>/);
});

test('malformed metadata is safe and counts are never fabricated', () => {
  for (const context of [false, [], 'bad', null]) {
    const html = renderToStaticMarkup(createElement(SchemeKnowledgeContext, { context }));
    assert.match(html, /not recorded for this saved Scheme/);
  }
  const html = renderToStaticMarkup(createElement(SchemeKnowledgeContext, { context: {
    status: 'published', publication_id: '<script>inert</script>', available_items: -1,
    included_items: '8', omitted_items: Infinity,
  } }));
  assert.match(html, /&lt;script&gt;inert&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>|<dd>(?:-1|8|Infinity)<\/dd>/);
  assert.equal((html.match(/<dd>Not recorded<\/dd>/g) ?? []).length, 3);
});

test('displayed metadata does not substitute another publication', () => {
  const old = { status: 'published', publication_id: 'historical-publication', available_items: 30,
    included_items: 5, omitted_items: 25 };
  const html = renderToStaticMarkup(createElement(SchemeKnowledgeContext, { context: old,
    latestContext: { status: 'published', publication_id: 'latest-publication', available_items: 1000 } }));
  assert.match(html, /historical-publication/);
  assert.doesNotMatch(html, /latest-publication|1000/);
});
