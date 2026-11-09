import assert from 'node:assert/strict';
import test from 'node:test';
import { filterKnowledge, importantKnowledge, knowledgeHref, knowledgeSourceHref, matchesPublication,
  parseKnowledgeResponse, parseKnowledgePreview, readKnowledgeLink, evidenceAvailability, type KnowledgeItem, type KnowledgeSnapshot } from '../src/knowledge.ts';
const item = (id: string, changes: Partial<KnowledgeItem> = {}): KnowledgeItem => ({ id, project_id: 2,
  statement: 'Preserve ligand chemistry.', category: 'REQUIREMENT', design_status: 'AGREED', scope: 'Project',
  rationale: 'Keep atom mappings.', source_state: 'valid', evidence: [
    { source_id: 'proposal', role: 'assistant', excerpt: 'Preserve the molecular graph?', label: 'Structure design' },
    { source_id: 'acceptance', role: 'user', excerpt: 'Agree.', label: 'Structure design' }], ...changes });
const snapshot = (): KnowledgeSnapshot => ({ project_id: 2, publication_id: 'paired', items: [item('one')],
  sources: [{ id: 'proposal', label: 'Chemical metadata', note_id: 5 }], coverage: { processed: ['conversation'], unavailable: [] } });
test('publication pairing requires matching projects and explicit nonblank identities', () => {
  assert.equal(matchesPublication(snapshot(), 2, 'paired'), true);
  for (const [projectId, publicationId] of [[1, 'paired'], [2, 'other'], [2, undefined]] as const)
    assert.equal(matchesPublication(snapshot(), projectId, publicationId), false);
  assert.equal(matchesPublication({ ...snapshot(), publication_id: undefined }, 2, undefined), false);
  assert.equal(matchesPublication({ ...snapshot(), publication_id: ' ' }, 2, ' '), false);
});
test('parser rejects foreign and malformed knowledge without changing source data', () => {
  const original = snapshot(); const before = structuredClone(original);
  assert.deepEqual(parseKnowledgeResponse({ snapshot: original }, 2).snapshot, original);
  assert.deepEqual(parseKnowledgeResponse({ snapshot: original }, 2).current_evidence_availability, {});
  assert.deepEqual(original, before);
  assert.equal(parseKnowledgeResponse({ snapshot: null }, 2).snapshot, null);
  assert.throws(() => parseKnowledgeResponse({}, 2));
  assert.throws(() => parseKnowledgeResponse({ snapshot: original }, 1));
  for (const changed of [
    { ...original, items: [item('one', { project_id: 3 })] },
    { ...original, items: [item('same'), item('same')] },
    { ...original, items: [item('one', { rationale: {} as string })] },
    { ...original, items: [item('one', { evidence: [{ source_id: 'x', role: 'user', excerpt: 'exact', label: {} as string }] })] },
    { ...original, sources: [{ id: 'same' }, { id: 'same' }] },
  ]) assert.throws(() => parseKnowledgeResponse({ snapshot: changed }, 2));
});
test('search combines excerpt/title matching with exact status, category and scope filters', () => {
  const first = item('first');
  const second = item('second', { design_status: 'PROPOSED', scope: 'First release', category: 'PROPOSAL', rationale: undefined });
  const entries = [first, second];
  assert.deepEqual(filterKnowledge(entries, '  ATOM\n mappings ', 'AGREED', 'REQUIREMENT', 'Project'), [first]);
  assert.deepEqual(filterKnowledge(entries, 'molecular graph'), entries);
  assert.deepEqual(filterKnowledge(entries, 'Chemical metadata', '', '', '', snapshot().sources), entries);
  assert.deepEqual(filterKnowledge(entries, '', 'PROPOSED', '', 'Project'), []);
  assert.deepEqual(filterKnowledge(entries, '', '', '', 'First release'), [second]);
  assert.deepEqual(filterKnowledge(entries), entries);
  assert.equal(second.rationale, undefined);
});
test('highlights keep scopes and statuses while excluding invalid, superseded or conflicting support', () => {
  const data = snapshot();
  data.items = [item('1'), item('2', { design_status: 'PROPOSED' }), item('3', { scope: 'First release' }),
    item('4', { design_status: 'UNRESOLVED', statement: 'Which protonation strategy?' }),
    item('5', { superseded_by: ['replacement'] }), item('6', { conflicts_with: ['conflict'] }), item('7')];
  const before = structuredClone(data);
  assert.deepEqual(importantKnowledge(data, { '7': 'unavailable' }).map((entry) => entry.id), ['1', '3', '4']);
  assert.deepEqual(data, before);
  assert.equal(evidenceAvailability(data.items[0], { '1': 'stale' }), 'stale');
  assert.equal(data.items[0].design_status, 'AGREED');
  data.items = Array.from({ length: 12 }, (_, index) => item(String(index), { statement: `Choice ${index}` }));
  assert.equal(importantKnowledge(data).length, 5);
});
test('knowledge links encode stable item and publication identity only', () => {
  const link = { projectId: 2, itemId: 'knowledge:id/+?x', publicationId: 'publication-one' };
  assert.deepEqual(readKnowledgeLink(knowledgeHref(link)), link);
  for (const hash of ['#history?project=2', '#knowledge?project=-1', '#knowledge?project=abc', '#knowledge?project=1.2'])
    assert.equal(readKnowledgeLink(hash), null);
});
test('overview selection also respects recorded incoming conflicts', () => {
  const data = snapshot();
  data.items = [item('accepted'), item('alternative', { design_status: 'UNKNOWN', conflicts_with: ['accepted'] })];
  assert.deepEqual(importantKnowledge(data), []);
  assert.equal(data.items[0].design_status, 'AGREED');
});
test('original-source links use existing project-scoped resolvers and require available descriptors', () => {
  assert.equal(knowledgeSourceHref(2, { id: 'x', note_id: 5 }, 'valid'), '#history?project=2&note=5');
  assert.equal(knowledgeSourceHref(2, { id: 'x', session_id: 7, turn_id: 'turn' }, 'valid'), '#history?project=2&session=7&turn=turn');
  assert.equal(knowledgeSourceHref(2, { id: 'x', note_id: 5 }, 'unavailable'), null);
  assert.equal(knowledgeSourceHref(2, { id: 'x', note_id: -1 }, 'valid'), null);
  assert.equal(knowledgeSourceHref(2, undefined, 'valid'), null);
});


test('unpublished previews require a paired project and safe brief source metadata', () => {
  const entry = { text: 'Preserve chemistry.', source_ids: ['proposal'], classification: 'reported' };
  const raw = { published: false, publication_id: 'paired', knowledge: snapshot(),
    brief: { generated_at: '2026-11-09T23:00:00Z', project_state: { publication_id: 'paired' },
      sections: { overview: [entry], current_status: [entry], architecture: [entry] },
      sources: [{ id: 'proposal', label: 'Original', text: 'Exact evidence.' }], coverage: [] } };
  const before = structuredClone(raw);
  assert.deepEqual(parseKnowledgePreview(raw, 2).knowledge, snapshot());
  assert.deepEqual(raw, before);
  for (const mutation of [
    (value: any) => { value.published = true; },
    (value: any) => { value.publication_id = 'different'; },
    (value: any) => { value.brief.project_state.publication_id = undefined; },
    (value: any) => { value.knowledge.project_id = 1; },
    (value: any) => { value.brief.sources[0].turn_id = 123; },
    (value: any) => { value.brief.sections.overview[0].classification = {}; },
    (value: any) => { value.brief.sources[0].shared_sessions = [{}]; },
  ]) {
    const changed = structuredClone(raw); mutation(changed);
    assert.throws(() => parseKnowledgePreview(changed, 2));
  }
});
