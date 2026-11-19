import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';

let server: ViteDevServer;
let knowledge: typeof import('../src/KnowledgeItems.tsx');
before(async () => {
  server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  knowledge = await server.ssrLoadModule('/src/KnowledgeItems.tsx') as typeof knowledge;
});
after(async () => { await server?.close(); });

test('the conversation fallback waits for an explicit search', () => {
  const markup = renderToStaticMarkup(createElement(knowledge.KnowledgeHistorySearch, { projectId: 6, query: 'missing detail' }));
  assert.match(markup, /<button[^>]*>\s*Search original conversations/);
  assert.doesNotMatch(markup, /Searching indexed conversations|Original conversation results/);
});

test('the fallback reuses the bounded project-scoped history search with cancellation', async () => {
  const query = 'RDKit & why?';
  const signal = new AbortController().signal;
  const data = { results: [], next_offset: 40 };
  const request = (async (url: string, options: RequestInit) => {
    const link = new URL(url, 'http://example.test');
    assert.equal(link.pathname, '/projects/6/history/search');
    assert.equal(link.searchParams.get('q'), query);
    assert.equal(link.searchParams.get('offset'), '20');
    assert.equal(options.signal, signal);
    assert.equal(options.method, undefined);
    return data;
  }) as typeof import('../src/api.ts').api;
  assert.equal(await knowledge.searchKnowledgeHistory(6, ` ${query} `, 20, signal, request), data);
});
