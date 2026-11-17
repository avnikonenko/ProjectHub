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
  assert.equal((html.match(/<details\b/g) ?? []).length, 4);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
  assert.match(html, /<summary>Coverage<\/summary><ul><li>4 cited sources<\/li><\/ul><details><summary>Detailed coverage<\/summary>/);
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

test('conversation evidence leads targeted code support without claiming a full audit', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, {
    diagnostics: {
      evidence_coverage: { repository },
      archive_conversations: { scope: 'linked_codex_sessions', linked_sessions: 2,
        read_sessions: 2, notes: ['History was summarized.'] },
    },
  }));
  assert(html.indexOf('Conversation evidence') < html.indexOf('Targeted code inspection'));
  assert.match(html, /2 of 2 linked Codex sessions read/);
  assert.match(html, /Selected code provides supporting context. This is not a full code audit/);
  assert.match(html, /<details><summary>History reading details<\/summary>/);
  assert.match(html, /<details><summary>Inspection scope and limits<\/summary>/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
});

test('scope notes deduplicate exact wording without merging distinct limitations', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, {
    diagnostics: { evidence_coverage: { repository: { ...repository, notes: [
      'Representative-file limit applied.', ' Representative-file   limit applied. ',
      'Only selected declarations were supplied.',
    ] } } },
  }));
  assert.equal((html.match(/Representative-file limit applied\./g) ?? []).length, 1);
  assert.doesNotMatch(html, /Representative-file   limit/);
  assert.match(html, /Only selected declarations were supplied/);
  const unavailable = renderToStaticMarkup(createElement(CoverageDetails, {
    diagnostics: { evidence_coverage: { repository: { status: 'not_used', notes: [] } } },
  }));
  assert.match(unavailable, /Code was not an input/);
  assert.doesNotMatch(unavailable, /Selected code provides supporting context/);
});

test('repository coverage separates input files, partial reads and omissions with exact scope', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, {
    diagnostics: { evidence_coverage: { repository }, extraction_workers: 10, model_requests: 247 },
  }));
  assert.match(html, /Excerpts from 5 of 16 eligible repository files supplied to AI<\/li><\/ul><details><summary>Detailed coverage/);
  assert.match(html, /5 file excerpts supplied from 20 tracked paths/);
  assert.match(html, /Code files: 3 of 12 eligible tracked code files read; 2 supplied only in part/);
  assert.match(html, /Files supplied only in part<\/dt><dd>3/);
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

test('a contradictory code category cannot inflate the displayed repository coverage', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, {
    diagnostics: { evidence_coverage: { repository: { ...repository,
      by_category: { code: { eligible_files: 100, read_files: 100, full_files: 100, partial_files: 0 } },
    } } },
  }));
  assert.match(html, /5 file excerpts supplied from 20 tracked paths/);
  assert.doesNotMatch(html, /100 of 100|Code files:/);
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
  assert.equal((html.match(/<details\b/g) ?? []).length, 2);
  assert.match(html, /Files read: 12 of 15/);
  assert.doesNotMatch(html, /not recorded for this saved result/);
});

test('current source checks cannot hide missing saved coverage measurements', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, {},
    createElement('details', null, createElement('summary', null, 'Current source availability'))));
  assert.match(html, /Exact repository file coverage was not recorded for this result/);
  assert.match(html, /Exact conversation coverage was not recorded for this result/);
});

test('per-file table separates a complete disk read from a sparse AI excerpt', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, { diagnostics: { evidence_coverage: {
    repository: { ...repository, file_details: [{ path: 'src/app.py', file_bytes: 5000, bytes_read: 5000,
      excerpt_chars: 300, local_read_complete: true, excerpt_complete: false,
      omission_reasons: ['declaration_selection', 'line_width_limit'] }] },
  } } }));
  assert.match(html, /<caption>Local file reads and supplied AI excerpts<\/caption>/);
  assert.match(html, /File size \(bytes\)/); assert.match(html, /AI excerpt \(characters\)/);
  assert.match(html, /5,000<\/td><td>5,000<\/td><td>Complete<\/td><td>300<\/td><td>Partial/);
  assert.match(html, /Only selected declarations supplied; Long lines shortened/);
  assert.match(html, /not a percentage of understanding/);
  assert.doesNotMatch(html, /declaration_selection|line_width_limit/);
  const legacy = renderToStaticMarkup(createElement(CoverageDetails, {
    diagnostics: { evidence_coverage: { repository } },
  }));
  assert.match(legacy, /Per-file read sizes and AI excerpt lengths were not recorded/);
  assert.doesNotMatch(legacy, /<caption>Local file reads/);
});

test('missing or invalid per-file details do not hide other recorded read paths', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, { diagnostics: { evidence_coverage: {
    repository: { ...repository, paths: { read: ['src/app.py', 'src/missing.py'], partial: ['src/missing.py'] },
      file_details: [{ path: 'src/app.py', file_bytes: 5000, bytes_read: 5000,
        excerpt_chars: 300, local_read_complete: true, excerpt_complete: false,
        omission_reasons: ['declaration_selection'] },
      { path: 'src/missing.py', file_bytes: 5, bytes_read: 10, excerpt_chars: 10,
        local_read_complete: true, excerpt_complete: true, omission_reasons: [] }] },
  } } }));
  assert.match(html, /Local file reads and supplied AI excerpts/);
  assert.match(html, /<h4>Read files<\/h4><ul><li><code>src\/missing.py<\/code><\/li><\/ul>/);
  assert.match(html, /<h4>Partly supplied files<\/h4><ul><li><code>src\/missing.py<\/code><\/li><\/ul>/);
  assert.equal((html.match(/src\/app.py/g) ?? []).length, 1);
});

test('registered conversation totals reveal sources beyond the saved bounded manifest', () => {
  const html = renderToStaticMarkup(createElement(CoverageDetails, { diagnostics: { evidence_coverage: {
    conversations: { inventory_scope: 'saved_source_manifest', known_sources: 60, processed_sources: 58,
      notes: [], source_inventory: { scope: 'registered_project_sources', linked_codex_sessions_total: 100,
        linked_codex_sessions_considered: 64, linked_codex_sessions_omitted_parent_limit: 36,
        imported_chats_total: 70, imported_chats_considered: 64, imported_chats_omitted_parent_limit: 6,
        omission_reasons: { active_turns: 2, parent_limit_linked_sessions: 36 }, notes: [] } },
  } } }));
  assert.match(html, /64 of 100 linked Codex sessions considered; 36 omitted by the source-count limit/);
  assert.match(html, /64 of 70 imported chats considered; 6 omitted by the source-count limit/);
  assert.match(html, /Within the saved source manifest, 58 of 60 source groups/);
  assert.match(html, /Considered sources may still have omitted messages/);
  assert.match(html, /Unfinished turns omitted<\/dt><dd>2/);
  assert.match(html, /must not be added together/);
  assert.doesNotMatch(html, /active_turns|parent_limit_linked_sessions/);
});
