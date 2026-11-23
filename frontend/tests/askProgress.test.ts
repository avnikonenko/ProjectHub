import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';
import type { Progress } from '../src/GenerationStatus';

let server: ViteDevServer;
let GenerationStatus: typeof import('../src/GenerationStatus')['GenerationStatus'];
let BriefSources: typeof import('../src/BriefReferences')['BriefSources'];
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  ({ GenerationStatus } = await server.ssrLoadModule('/src/GenerationStatus.tsx'));
  ({ BriefSources } = await server.ssrLoadModule('/src/BriefReferences.tsx'));
});
after(async () => { await server?.close(); });

function render(state: Progress['state'], request_id?: string, askRequestId: string | null = 'local-ask') {
  const sharedProgress: Progress = {
    state, request_id, started_at: '2026-11-23T00:00:00Z', stage: 'Generating Scheme answer',
    sessions: 0, error: 'Request-specific error',
  };
  return renderToStaticMarkup(createElement(GenerationStatus, {
    base: '/projects/1', kind: 'ask', label: 'Scheme answer', sharedProgress, askRequestId,
  }));
}

test('only a matching Ask request displays named progress and cancellation', () => {
  const owned = render('running', 'local-ask');
  assert.match(owned, /Scheme answer/);
  assert.match(owned, /Cancel generation/);
  for (const foreign of [
    render('running', 'another-ask'), render('running'), render('running', 'local-ask', null),
  ]) {
    assert.match(foreign, /Another question for this project is running/);
    assert.doesNotMatch(foreign, /Scheme answer|Cancel generation/);
  }
});

test('a prior or unrelated Ask terminal status stays out of the panel', () => {
  assert.match(render('failed', 'local-ask'), /Request-specific error/);
  assert.equal(render('failed', 'another-ask'), '');
  assert.equal(render('cancelled', 'another-ask'), '');
  assert.equal(render('cancelled', undefined, null), '');
});

test('Scheme evidence availability is shown as a historical status', () => {
  const html = renderToStaticMarkup(createElement(BriefSources, {
    prefix: '1-ask-scheme', sources: [
      { id: '1', label: 'Saved evidence', text: 'Saved source excerpt',
        evidence_availability: 'stale', evidence_availability_as_of: '2026-11-23T00:00:00Z' },
      { id: '2', label: 'Current passage', text: 'Current source excerpt' },
    ],
  }));
  assert.match(html, /Source status when Scheme was saved: stale/);
  assert.equal((html.match(/Source status when Scheme was saved/g) || []).length, 1);
});
