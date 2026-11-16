import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';
let server: ViteDevServer;
let details: any;
let timing: any;
let selectInspection: any;
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] }, server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  const module = await server.ssrLoadModule('/src/SchemeInspectionDetails.tsx');
  details = module.SchemeInspectionDetails;
  timing = module.SchemeInspectionTiming;
  selectInspection = module.schemeInspectionForDisplay;
});
after(async () => { await server?.close(); });
const coverage = { partial: true, limitations: [], files_inspected: 7, conversation_excerpts: 0,
  file_counts: { repository_paths_total: 100, excluded_paths: 10, safe_paths: 90, eligible_files: 20,
    policy_omitted_paths: 70, attempted_files: 8, read_files: 7, prefix_read_files: 2, failed_reads: 1,
    not_attempted_files: 12, evidence_files: 5, read_not_in_evidence: 2, listing_complete: true },
  inspection_policy: { max_read_bytes: 1000, max_inspection_seconds: 2.5, max_file_bytes: 300,
    max_source_chars: 250, max_package_chars: 1500, max_tree_paths: 100, max_metadata_bytes: 400,
    max_metadata_seconds: 0.25 } };
test('inspection details show recorded pipeline and limits in closed disclosures', () => {
  const html = renderToStaticMarkup(createElement(details, { coverage, saved: true }));
  assert.match(html, /^<details[^>]*><summary>How ProjectHub inspected this repository<\/summary>/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
  assert.match(html, /Inspection recorded with this saved Scheme/);
  assert.match(html, /Repository paths listed<\/dt><dd>100/);
  assert.match(html, /Prefix-only reads \(subset of successful reads\)<\/dt><dd>2/);
  assert.match(html, /Files in final evidence package<\/dt><dd>5/);
  assert.match(html, /1000 bytes/);
  assert.match(html, /2.5 seconds/);
  assert.match(html, /do not add them again/);
  assert.doesNotMatch(html, /\d+%/);
});
test('legacy, null and malformed counts do not invent zero or expose invalid numbers', () => {
  for (const file_counts of [undefined, null, false, { read_files: -1, attempted_files: '8',
    evidence_files: NaN, prefix_read_files: 1.5, repository_paths_total: null, listing_complete: 'true' }]) {
    const html = renderToStaticMarkup(createElement(details, { coverage: { ...coverage, file_counts }, saved: true }));
    assert.doesNotMatch(html, /<dd>(?:0|-1|8|NaN|1.5|Yes)<\/dd>/);
    assert.match(html, /not recorded|Not recorded/);
  }
  const html = renderToStaticMarkup(createElement(details, { coverage: { ...coverage,
    file_counts: { ...coverage.file_counts, read_files: 0, repository_paths_total: null } }, saved: false }));
  assert.match(html, /Files successfully read<\/dt><dd>0/);
  assert.match(html, /Repository paths listed<\/dt><dd>Not recorded/);
  assert.match(html, /Current repository inspection/);
});
test('saved and historical accounting never falls through to latest inspection', () => {
  const current = { coverage, diagnostics: { inspection_seconds: 0.25, bytes_read: 2000 } };
  const legacy = { coverage: { partial: false, limitations: [], files_inspected: 3, conversation_excerpts: 0 } };
  assert.deepEqual(selectInspection(legacy, current), { coverage: legacy.coverage, diagnostics: undefined, saved: true });
  const historical = { coverage: { ...coverage, file_counts: { ...coverage.file_counts, read_files: 4 } },
    diagnostics: { inspection_seconds: 9, bytes_read: 100 } };
  const selected = selectInspection(historical, current);
  assert.equal(selected.coverage.file_counts.read_files, 4);
  assert.equal(selected.diagnostics.inspection_seconds, 9);
  assert.equal(selectInspection(null, current).coverage, current.coverage);
  assert.equal(selectInspection(null, current).saved, false);
  const html = renderToStaticMarkup(createElement(timing, selected));
  assert.match(html, /Recorded with this saved Scheme/);
  assert.match(html, /9 seconds/);
  assert.doesNotMatch(html, /0.25|2000/);
  assert.equal(renderToStaticMarkup(createElement(timing, { saved: true })), '');
});

test('separately disclosed current counts clearly differ from saved evidence', () => {
  const html = renderToStaticMarkup(createElement(details, { coverage, saved: false, current: true }));
  assert.match(html, /<summary>Current repository inspection<\/summary>/);
  assert.match(html, /these counts do not describe the evidence used by the saved Scheme/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
});

test('malformed legacy limitations remain safe and stop reasons use plain words', () => {
  for (const limitations of ['invalid', null, { length: 2 }, [null, 8, 'Recorded limitation']]) {
    const html = renderToStaticMarkup(createElement(details, { coverage: { ...coverage, limitations,
      inspection_stopped: 'read_byte_limit' }, saved: true }));
    assert.match(html, /Inspection stopped: read byte limit/);
    assert.doesNotMatch(html, /read_byte_limit/);
    if (Array.isArray(limitations)) assert.match(html, /Recorded limitation/);
  }
});
