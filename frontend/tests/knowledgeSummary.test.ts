import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';
import { parseKnowledgeResponse, parseKnowledgeSummary } from '../src/knowledge.ts';
import { activateKnowledgeResult } from '../src/knowledgePublication.ts';

function brief(publication = 'knowledge-one') {
  const entry = { text: 'Detailed chemical requirements.', classification: 'reported', source_ids: ['original'] };
  return { generated_at: '2026-11-17T10:00:00Z', project_state: { publication_id: publication },
    sections: { overview: [entry], current_status: [], architecture: [], changes: [], open_questions: [], decision_candidates: [] },
    sources: [{ id: 'original', label: 'Original decision', text: 'Retain the molecular graph.' }], coverage: [] };
}
function snapshot(summary: unknown = brief()) {
  return { project_id: 2, publication_id: 'knowledge-one', items: [{ id: 'choice', project_id: 2,
    statement: 'Preserve chemistry.', category: 'REQUIREMENT', design_status: 'AGREED', scope: 'Project', evidence: [] }],
    sources: [], summary };
}

test('a matching saved Knowledge Summary survives reload separately from records and overview pairing', () => {
  const original = snapshot();
  const before = structuredClone(original);
  const parsed = parseKnowledgeResponse({ snapshot: original, paired_with_current_brief: false }, 2);
  assert.deepEqual(parsed.snapshot?.summary, brief());
  assert.equal(parsed.paired_with_current_brief, false);
  assert.equal(parsed.snapshot?.items.length, 1);
  assert.equal(parsed.snapshot?.summary_error, undefined);
  assert.deepEqual(original, before);
});

test('invalid or foreign summaries never hide valid Knowledge records or impersonate a matching publication', () => {
  const badSources = brief();
  badSources.sources[0] = { ...badSources.sources[0], project_id: 3 } as any;
  for (const summary of [brief('other-publication'), { ...brief(), project_id: 3 }, badSources,
    { ...brief(), sections: [] }, { ...brief(), sources: [{}] }, { ...brief(), generated_at: {} }]) {
    const parsed = parseKnowledgeResponse({ snapshot: snapshot(summary) }, 2);
    assert.equal(parsed.snapshot?.items.length, 1);
    assert.equal(parsed.snapshot?.summary, undefined);
    assert.match(parsed.snapshot?.summary_error || '', /does not match|unavailable/);
    assert.throws(() => parseKnowledgeSummary(summary, 2, 'knowledge-one'));
  }
});

test('legacy Knowledge without a saved summary remains usable and does not borrow a general overview', () => {
  const original = snapshot();
  delete (original as any).summary;
  const parsed = parseKnowledgeResponse({ snapshot: original, paired_with_current_brief: true,
    brief: brief('unrelated-overview') }, 2);
  assert.equal(parsed.snapshot?.summary, undefined);
  assert.equal(parsed.snapshot?.summary_error, undefined);
  assert.equal(parsed.snapshot?.items.length, 1);
});

test('saving a result returns its durable matching summary through the existing activation contract', async () => {
  const requests: { path: string; body: unknown }[] = [];
  const result = await activateKnowledgeResult('/projects/2', 2, 'knowledge-one', new AbortController().signal,
    async (path, options) => {
      requests.push({ path, body: JSON.parse(options.body as string) });
      return { published: true, publication_id: 'knowledge-one', knowledge: snapshot(), brief: brief() };
    });
  assert.deepEqual(requests, [{ path: '/projects/2/codex/analysis/publish', body: { publication_id: 'knowledge-one' } }]);
  assert.deepEqual(result.knowledge.summary, brief());
});

let server: ViteDevServer;
let PublishedKnowledgeSummary: any;
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  PublishedKnowledgeSummary = (await server.ssrLoadModule('/src/KnowledgePage.tsx')).PublishedKnowledgeSummary;
});
after(async () => { await server?.close(); });

test('the saved summary has its own title and calls the shared renderer in the published context', () => {
  const current = parseKnowledgeResponse({ snapshot: snapshot() }, 2).snapshot!;
  const calls: string[] = [];
  const html = renderToStaticMarkup(createElement(PublishedKnowledgeSummary, {
    snapshot: current, renderBrief: (value: unknown, context: string) => {
      assert.equal(value, current.summary); calls.push(context);
      return createElement('p', null, 'Detailed chemical requirements.');
    },
  }));
  assert.match(html, /<h2>Knowledge Summary<\/h2>/);
  assert.match(html, /Saved separately from Project Overview/);
  assert.match(html, /Detailed chemical requirements/);
  assert.deepEqual(calls, ['published']);
  assert.doesNotMatch(html, /Overview preview|Use this result/);
});

test('legacy and mismatched summary displays never render unrelated content', () => {
  const renderBrief = () => { throw new Error('An unrelated summary must not render'); };
  const legacy = snapshot(); delete (legacy as any).summary;
  assert.equal(renderToStaticMarkup(createElement(PublishedKnowledgeSummary, { snapshot: legacy, renderBrief })), '');
  const mismatched = snapshot(brief('other-publication'));
  const html = renderToStaticMarkup(createElement(PublishedKnowledgeSummary, { snapshot: mismatched, renderBrief }));
  assert.match(html, /does not match this Knowledge publication/);
  assert.doesNotMatch(html, /Detailed chemical requirements/);
});
