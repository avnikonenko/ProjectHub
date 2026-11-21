import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';

let server: ViteDevServer;
let GenerationStatus: any;
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  ({ GenerationStatus } = await server.ssrLoadModule('/src/GenerationStatus.tsx'));
});
after(async () => { await server?.close(); });

function render(live_token_usage: unknown, state = 'running') {
  return renderToStaticMarkup(createElement(GenerationStatus, { base: '/projects/1', kind: 'knowledge',
    sharedProgress: { state, stage: 'Extracting source-linked knowledge', sessions: 0, live_token_usage },
  }));
}

test('live token status distinguishes waiting, known zero and partial reports', () => {
  const starting = render({ total_tokens: null, reported_requests: 0, model_requests: 0 });
  assert.match(starting, /Waiting for a reported token total<\/p>/);
  assert.doesNotMatch(starting, /0 of 0 model requests/);
  const waiting = render({ total_tokens: null, reported_requests: 0, model_requests: 2 });
  assert.match(waiting, /Waiting for a reported token total · 0 of 2 model requests reported a total/);
  const zero = render({ total_tokens: 0, reported_requests: 1, model_requests: 2 });
  assert.match(zero, /Reported tokens so far: 0 · 1 of 2 model requests reported a total/);
  const counting = render({ total_tokens: 234, reported_requests: 2, model_requests: 3 });
  assert.match(counting, /Reported tokens so far: 234 · 2 of 3 model requests reported a total/);
});

test('legacy, inconsistent, and completed statuses omit live token figures', () => {
  for (const result of [
    render(undefined), render({ total_tokens: null, reported_requests: 1, model_requests: 2 }),
    render({ total_tokens: 1, reported_requests: 2, model_requests: 1 }),
    render({ total_tokens: -1, reported_requests: 1, model_requests: 1 }),
    render({ total_tokens: 10, reported_requests: 1, model_requests: 1 }, 'completed'),
  ]) assert.doesNotMatch(result, /Reported tokens|Waiting for a reported token total/);
});
