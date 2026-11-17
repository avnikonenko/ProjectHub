import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';
let server: ViteDevServer;
let CoverageDetails: any;
before(async () => {
  server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true, include: [] } });
  CoverageDetails = (await server.ssrLoadModule('/src/CoverageDetails.tsx')).CoverageDetails;
});
after(async () => { await server?.close(); });
test('coverage leads with input availability and nests processing diagnostics', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, {
    savedAt: '2026-11-17T10:00:00Z', sourceCount: 4, coverage: ['One linked session was unavailable.'],
    diagnostics: { duration_seconds: 12.5, input_records: 30, input_chars: 25000, archive_cache_hits: 3 },
  }));
  assert.equal((html.match(/<details\b/g) ?? []).length, 3);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
  assert.match(html, /Coverage &amp; technical details/);
  assert.match(html, /12\.5 seconds/);
  assert.match(html, /Input records/); assert.match(html, /25,000/);
  assert.match(html, /One linked session was unavailable/);
  assert.match(html, /Cited sources in this result: 4/);
  assert.match(html, /<summary>Processing diagnostics<\/summary>/);
  assert(html.indexOf('Exact repository file coverage') < html.indexOf('Processing diagnostics'));
  assert.match(html, /Citation counts do not measure input coverage/);
  assert.doesNotMatch(html, /<summary>Generation details/);
});
test('old results do not invent zero processing coverage', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, { sourceCount: 2 }));
  assert.match(html, /Exact repository file coverage was not recorded for this result/);
  assert.match(html, /Exact conversation coverage was not recorded for this result/);
  assert.doesNotMatch(html, /0%|0 of|Input records|Fresh extraction requests/);
});

const repository = {
  status: 'available', inventory_scope: 'git_tracked', inventory_complete: true,
  total_files: 20, eligible_files: 16, read_files: 5, full_files: 2, partial_files: 3,
  ai_input_files: 5, excluded_files: 1, unsupported_files: 3, not_selected_files: 9,
  failed_files: 1, empty_files: 1,
  by_category: { code: { eligible_files: 12, read_files: 3, full_files: 1, partial_files: 2 } },
  paths: { read: ['src/app.py'], partial: ['src/app.py'], not_selected: ['src/extra.py'], failed: ['src/bad.py'], empty: ['src/empty.py'] },
  paths_truncated: true, notes: ['Representative-file limit applied.'],
};

test('repository coverage separates input files, partial reads and omissions with exact scope', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, {
    diagnostics: { evidence_coverage: { repository }, extraction_workers: 10, model_requests: 247 },
  }));
  assert.match(html, /5 file excerpts supplied from 20 tracked paths/);
  assert.match(html, /Code files: 3 of 12 eligible tracked code files read; 2 only in part/);
  assert.match(html, /Files read only in part<\/dt><dd>3/);
  assert.match(html, /Eligible files not selected<\/dt><dd>9/);
  assert.match(html, /Files excluded by inspection rules<\/dt><dd>1/);
  assert.match(html, /Files used and missed/); assert.match(html, /src\/extra.py/);
  assert.match(html, /File lists are shortened/);
  assert.match(html, /Untracked paths, including ignored untracked files/);
  assert.match(html, /not whether every detail was retained or the conclusions are correct/);
  assert(html.indexOf('5 file excerpts') < html.indexOf('Processing diagnostics'));
  assert(html.indexOf('Extraction workers') > html.indexOf('Processing diagnostics'));
});

test('unknown inventory and conversation-only Knowledge do not imply full code coverage', () => {
  const partial = renderToStaticMarkup(createElement(CoverageDetails, {
    diagnostics: { evidence_coverage: { repository: { ...repository, inventory_complete: false } } },
  }));
  assert.match(partial, /5 file excerpts supplied; the complete tracked-file count is unknown/);
  assert.doesNotMatch(partial, /5 file excerpts supplied from 20 tracked paths/);
  const knowledge = renderToStaticMarkup(createElement(CoverageDetails, {
    diagnostics: { evidence_coverage: { repository: { status: 'not_used', notes: [] } },
      source_selection: { selected_primary_fragments: 6, available_primary_fragments: 10, omitted_primary_fragments: 4 } },
  }));
  assert.match(knowledge, /Code was not an input to these Knowledge records/);
  assert.match(knowledge, /6 of 10 available conversation fragments selected for extraction; 4 omitted/);
  assert.match(knowledge, /not separate conversations/);
  assert.doesNotMatch(knowledge, /0 of|100%|full coverage/);
});

test('archive coverage distinguishes linked sessions, deduplicated and unfinished turns', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, { diagnostics: {
    archive_conversations: { scope: 'linked_codex_sessions', linked_sessions: 3, read_sessions: 2,
      finished_turns: 100, unique_turns: 75, duplicate_turns_omitted: 25, active_turns_skipped: 1,
      notes: ['Imported ChatGPT documents are not counted here.'] },
  } }));
  assert.match(html, /2 of 3 linked Codex sessions read/);
  assert.match(html, /Distinct finished turns included<\/dt><dd>75/);
  assert.match(html, /Repeated inherited turns collapsed<\/dt><dd>25/);
  assert.match(html, /Unfinished turns omitted<\/dt><dd>1/);
  assert.match(html, /Imported ChatGPT documents are not counted here/);
});

test('contradictory fragment counts never display a coverage fraction', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, { diagnostics: {
    source_selection: { available_primary_fragments: 10, selected_primary_fragments: 9, omitted_primary_fragments: 3 },
  } }));
  assert.doesNotMatch(html, /9 of 10/);
});
test('malformed metadata is ignored and coverage strings remain escaped data', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, {
    savedAt: 'invalid date', sourceCount: -1, coverage: { limitations: ['<script>inert</script>', 5], processed: null },
    diagnostics: { duration_seconds: -1, input_records: -2, input_chars: Infinity },
  }));
  assert.match(html, /&lt;script&gt;inert&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>|Invalid Date|Cited sources|Infinity/);
});
test('view-specific saved measurements use an explicit custom coverage contract', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, { customCoverage: true, note: 'Current inspection, separate from the saved result.' },
    createElement('p', null, 'Files read: 12 of 15 eligible files.')));
  assert.equal((html.match(/<details\b/g) ?? []).length, 1);
  assert.match(html, /Files read: 12 of 15/);
  assert.doesNotMatch(html, /not recorded for this saved result/);
});

test('current source checks cannot hide missing saved coverage measurements', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, {},
    createElement('details', null, createElement('summary', null, 'Current source availability'))));
  assert.match(html, /Exact repository file coverage was not recorded for this result/);
  assert.match(html, /Exact conversation coverage was not recorded for this result/);
});
