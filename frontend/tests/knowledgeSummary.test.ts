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
let KnowledgePage: any;
let PublishedKnowledgeSummary: any;
let EarlierKnowledgeResult: any;
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  ({ KnowledgePage, PublishedKnowledgeSummary, EarlierKnowledgeResult } = await server.ssrLoadModule('/src/KnowledgePage.tsx'));
});
after(async () => { await server?.close(); });

test('Knowledge omits the earlier summary section when there is no legacy result', () => {
  const html = renderToStaticMarkup(createElement(KnowledgePage, { base: '/projects/2', generation: null }));
  assert.match(html, /Project Knowledge/);
  assert.doesNotMatch(html, /Unpublished Knowledge previews|No unpublished preview is available|Earlier Knowledge Summary/);
});

test('the saved Knowledge Summary has a narrative card followed by a supporting details card', () => {
  const current = parseKnowledgeResponse({ snapshot: snapshot() }, 2).snapshot!;
  const html = renderToStaticMarkup(createElement(PublishedKnowledgeSummary, { snapshot: current }));
  assert.match(html, /<h2>Knowledge Summary<\/h2>/);
  assert.match(html, /Saved separately from Project Overview/);
  assert.match(html, /Detailed chemical requirements.*<\/section><[^>]*class="card result-support">/);
  assert.match(html, /<summary>Coverage<\/summary>/);
  assert.match(html, /Knowledge Summary sources \(1\)/);
  assert.match(html, /Retain the molecular graph\./);
  assert.doesNotMatch(html, /Overview preview|Use this result/);
});

test('legacy and mismatched summary displays never render unrelated content', () => {
  const legacy = snapshot(); delete (legacy as any).summary;
  assert.equal(renderToStaticMarkup(createElement(PublishedKnowledgeSummary, { snapshot: legacy })), '');
  const mismatched = snapshot(brief('other-publication'));
  const html = renderToStaticMarkup(createElement(PublishedKnowledgeSummary, { snapshot: mismatched }));
  assert.match(html, /does not match this Knowledge version/);
  assert.doesNotMatch(html, /Detailed chemical requirements|result-support/);
});

test('archived Knowledge Summary cites saved excerpts without offering unchecked original links', () => {
  const summary = brief();
  Object.assign(summary.sources[0], { note_id: 14, share: true });
  const current = parseKnowledgeResponse({ snapshot: snapshot(summary) }, 2).snapshot!;
  const active = renderToStaticMarkup(createElement(PublishedKnowledgeSummary, { snapshot: current }));
  const historical = renderToStaticMarkup(createElement(PublishedKnowledgeSummary, { snapshot: current, historical: true }));
  assert.match(active, /Read full transcript/);
  assert.match(historical, /Retain the molecular graph\./);
  assert.match(historical, /Current source availability has not been checked/);
  assert.doesNotMatch(historical, /Read full transcript/);
});

test('earlier saved Knowledge retains content, sources and coverage as a read-only version', () => {
  const result = { published: false, publication_id: 'knowledge-one',
    knowledge: { ...snapshot(), generated_at: brief().generated_at, coverage: { limitations: ['Earlier bounded extraction.'] },
      extraction_version: 'earlier-extractor' },
    brief: brief(), diagnostics: { duration_seconds: 5 }, baseline_brief: null };
  const before = structuredClone(result);
  let downloads = 0;
  const html = renderToStaticMarkup(createElement(EarlierKnowledgeResult, { result, onDownload: () => { downloads++; } }));
  assert.match(html, /Earlier Knowledge Summary/);
  assert.match(html, /Saved result/);
  assert.match(html, /Download JSON/);
  assert.match(html, /role="tab"[^>]*>Summary/);
  assert.match(html, /Knowledge records/);
  assert.match(html, /Retain the molecular graph/);
  assert.match(html, /Preserve chemistry/);
  assert.match(html, /Earlier bounded extraction/);
  assert.equal((html.match(/<summary>Coverage<\/summary>/g) ?? []).length, 2);
  assert.match(html, /Earlier version sources \(1\)/);
  assert.match(html, /Extraction version <code>earlier-extractor<\/code>/);
  assert.doesNotMatch(html, /Knowledge coverage|<summary>Current source availability<\/summary>/);
  assert.doesNotMatch(html.replace(/<[^>]*>/g, ' '), /[Pp]ublished|[Uu]npublished|[Pp]review|Save Knowledge Summary|Use this result/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
  const buttons = [...html.matchAll(/<button\b[^>]*>([\s\S]*?)<\/button>/g)].map(match => match[1].replace(/<[^>]*>/g, ' '));
  assert(buttons.includes('Download JSON') && buttons.includes('Summary') && buttons.includes('Knowledge records'));
  assert(buttons.every(label => !/Save|Use this|Generate|Delete|Remove/i.test(label)));
  assert.equal(downloads, 0);
  assert.deepEqual(result, before);
});
