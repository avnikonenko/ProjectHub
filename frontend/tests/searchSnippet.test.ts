import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';
import type { SearchSnippet } from '../src/SearchSnippet.tsx';

let server: ViteDevServer;
let Snippet: typeof SearchSnippet;
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  ({ SearchSnippet: Snippet } = await server.ssrLoadModule('/src/SearchSnippet.tsx'));
});
after(async () => { await server?.close(); });

test('marks matched search fragments while escaping text and preserving literal brackets', () => {
  const html = renderToStaticMarkup(createElement(Snippet, { snippet: 'fallback', snippet_parts: [
    { text: '[Keep] ', match: false }, { text: 'Search <phrase>', match: true },
    { text: ' & continue…', match: false },
  ] }));
  assert.equal(html, '<p>[Keep] <mark>Search &lt;phrase&gt;</mark> &amp; continue…</p>');
});

test('displays a plain snippet when match fragments are unavailable', () => {
  const html = renderToStaticMarkup(createElement(Snippet, { snippet: '[Example] <code>' }));
  assert.equal(html, '<p>[Example] &lt;code&gt;</p>');
});
