import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseArchiveConversations, parseEvidenceCoverage } from '../src/evidenceCoverage.ts';
import { parseGenerationDiagnostics } from '../src/generationDiagnostics.ts';

const repo = { status: 'available', inventory_scope: 'git_tracked', inventory_complete: true,
  total_files: 10, eligible_files: 8, read_files: 3, full_files: 1, partial_files: 2,
  ai_input_files: 3, notes: ['Bounded excerpts.'], paths: { read: ['src/app.py'] } };

test('saved coverage survives parsing without mutating metadata', () => {
  const raw = { evidence_coverage: { repository: repo, conversations: {
    inventory_scope: 'saved_source_manifest', known_sources: 4, processed_sources: 3, partial_sources: 1,
    available_fragments: 100, selected_fragments: 80, omitted_fragments: 20, notes: [] } } };
  const before = structuredClone(raw);
  assert.deepEqual(parseGenerationDiagnostics(raw), raw);
  assert.deepEqual(raw, before);
});

test('invalid or impossible repository measurements cannot become coverage proportions', () => {
  for (const change of [{ read_files: 11 }, { ai_input_files: 4 }, { full_files: 3 }, { eligible_files: 2 },
    { full_files: 100, partial_files: undefined }, { excluded_files: 100 }, { not_selected_files: 100 }])
    assert.equal(parseEvidenceCoverage({ repository: { ...repo, ...change } }), undefined);
  const safe = parseEvidenceCoverage({ repository: { status: 'available', total_files: true,
    read_files: -1, eligible_files: Infinity, inventory_complete: 'true', notes: [5, 'Safe'],
    paths: { read: ['<script>text</script>', 1], excluded: ['private.env'] } } })!.repository!;
  assert.equal(safe.total_files, undefined); assert.equal(safe.read_files, undefined);
  assert.equal(safe.inventory_complete, undefined);
  assert.deepEqual(safe.notes, ['Safe']); assert.deepEqual(safe.paths, { read: ['<script>text</script>'] });
});

test('paths and notes are bounded while complete counts remain intact', () => {
  const parsed = parseEvidenceCoverage({ repository: { ...repo, paths: {
    read: Array.from({ length: 150 }, (_, index) => `src/${index}.py`) } } })!.repository!;
  assert.equal(parsed.paths!.read!.length, 100);
  assert.equal(parsed.paths_truncated, true);
  assert.equal(parsed.total_files, 10);
});

test('archive totals and scoped source counts reject contradictory inputs', () => {
  assert.equal(parseArchiveConversations({ scope: 'all_conversations' }), undefined);
  assert.equal(parseArchiveConversations({ scope: 'linked_codex_sessions', linked_sessions: 2, read_sessions: 3 }), undefined);
  assert.equal(parseArchiveConversations({ scope: 'linked_codex_sessions', finished_turns: 10,
    unique_turns: 8, duplicate_turns_omitted: 3 }), undefined);
  assert.equal(parseEvidenceCoverage({ conversations: { inventory_scope: 'saved_source_manifest',
    known_sources: 2, processed_sources: 3 } }), undefined);
  assert.equal(parseEvidenceCoverage({ conversations: { inventory_scope: 'saved_source_manifest',
    available_fragments: 2, selected_fragments: 3 } }), undefined);
});

const fileDetail = { path: 'src/app.py', file_bytes: 5000, bytes_read: 5000, excerpt_chars: 300,
  local_read_complete: true, excerpt_complete: false, omission_reasons: ['declaration_selection'] };
const sourceInventory = { scope: 'registered_project_sources', codex_sessions_total: 100,
  linked_codex_sessions_total: 95, codex_sessions_considered: 64, linked_codex_sessions_considered: 60,
  unlinked_codex_sessions_considered: 4, codex_sessions_omitted_parent_limit: 36,
  linked_codex_sessions_omitted_parent_limit: 35, imported_chats_total: 70, imported_chats_considered: 64,
  imported_chats_omitted_parent_limit: 6, document_notes_total: 80, document_notes_considered: 64,
  other_documents_considered: 0, omission_reasons: { parent_limit_linked_sessions: 35, active_turns: 2 }, notes: [] };

test('local read bytes and supplied characters remain distinct and persist in diagnostics', () => {
  const raw = { evidence_coverage: { repository: { ...repo, file_details: [fileDetail] },
    conversations: { inventory_scope: 'saved_source_manifest', source_inventory: sourceInventory, notes: [] } } };
  const before = structuredClone(raw);
  assert.deepEqual(parseGenerationDiagnostics(raw), raw);
  assert.deepEqual(raw, before);
  // Structural line labels may make the excerpt longer than a tiny source.
  const tiny = parseEvidenceCoverage({ repository: { ...repo, file_details: [{ ...fileDetail,
    file_bytes: 2, bytes_read: 2, excerpt_chars: 10 }] } })!.repository!;
  assert.equal(tiny.file_details![0].excerpt_chars, 10);
});

test('malformed per-file details are omitted without replacing saved aggregate measurements', () => {
  const invalid = [null, { ...fileDetail, bytes_read: 5001 }, { ...fileDetail, file_bytes: '5000' },
    { ...fileDetail, excerpt_chars: -1 }, { ...fileDetail, local_read_complete: 'true' },
    { ...fileDetail, bytes_read: 4000, local_read_complete: true },
    { ...fileDetail, local_read_complete: false },
    { ...fileDetail, excerpt_complete: true },
    { ...fileDetail, bytes_read: 4000, local_read_complete: false, excerpt_complete: true }];
  const parsed = parseEvidenceCoverage({ repository: { ...repo, file_details: [...invalid, fileDetail, fileDetail] } })!.repository!;
  assert.deepEqual(parsed.file_details, [fileDetail]);
  assert.equal(parsed.ai_input_files, 3);
  assert.equal(parseEvidenceCoverage({ repository: repo })!.repository!.file_details, undefined);
});

test('source inventory counts include parent-limit omissions without borrowing manifest totals', () => {
  const parse = (inventory: unknown) => parseEvidenceCoverage({ conversations: {
    inventory_scope: 'saved_source_manifest', known_sources: 12, processed_sources: 10,
    source_inventory: inventory, notes: [] } })!.conversations!;
  assert.equal(parse(sourceInventory).source_inventory!.linked_codex_sessions_total, 95);
  assert.equal(parse(sourceInventory).known_sources, 12);
  for (const change of [{ linked_codex_sessions_considered: 96 }, { imported_chats_omitted_parent_limit: 7 },
    { document_notes_considered: 100 }, { unlinked_codex_sessions_considered: 8 },
    { other_documents_considered: 1 }]) {
    const parsed = parse({ ...sourceInventory, ...change });
    assert.equal(parsed.source_inventory, undefined);
    assert.equal(parsed.known_sources, 12);
  }
  assert.equal(parse(undefined).source_inventory, undefined);
});
