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
