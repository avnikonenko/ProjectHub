import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';
import type { HistoryMarkdown } from '../src/ui';

let server: ViteDevServer;
let History: typeof HistoryMarkdown;
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  ({ HistoryMarkdown: History } = await server.ssrLoadModule('/src/ui.tsx'));
});
after(async () => { await server?.close(); });

const context = '# Context from my IDE setup:\n\n## Active file: README.md\n';
const marker = '## My request for Codex:';

test('IDE context folds while the actual request stays visible', () => {
  const message = [context, '```md', marker, 'Example in selection', '```', marker, 'Fix **wording**.'].join('\r\n');
  const html = renderToStaticMarkup(createElement(History, { role: 'user', text: message }));
  assert.match(html, /<details class="history-ide-context"><summary>IDE context<\/summary>/);
  assert.match(html, /README\.md[\s\S]*Example in selection[\s\S]*<\/details><div class="markdown"><p>Fix <strong>wording<\/strong>\./);
});

test('context with no request boundary remains visible and ordinary messages render as before', () => {
  const text = context + 'More saved text';
  const incomplete = renderToStaticMarkup(createElement(History, { role: 'user', text }));
  assert.match(incomplete, /class="history-ide-context"/);
  assert.match(incomplete, /More saved text/);
  assert.doesNotMatch(incomplete, /<details/);
  const reply = renderToStaticMarkup(createElement(History, { role: 'assistant', text }));
  assert.doesNotMatch(reply, /history-ide-context/);
});
