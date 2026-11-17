import assert from 'node:assert/strict';
import test from 'node:test';
import { filterKnowledge, type KnowledgeItem } from '../src/knowledge.ts';

const item = (id: string, scope: string, changes: Partial<KnowledgeItem> = {}): KnowledgeItem => ({
  id, project_id: 2, statement: 'Preserve selected chemistry.', category: 'REQUIREMENT',
  design_status: 'AGREED', scope, rationale: 'Preserve atom identity.', source_state: 'valid',
  evidence: [{ source_id: 'original', role: 'user', excerpt: 'Preserve atom identity.' }],
  ...changes,
});

test('scope search finds multiple distinct CDK2 scopes without merging records', () => {
  const entries = [item('project', 'CDK2 project'), item('pilot', 'CDK2 pilot'),
    item('other', 'CDK9 pilot'), item('statement-only', 'General', { statement: 'Discuss CDK2.' })];
  const before = structuredClone(entries);

  assert.deepEqual(filterKnowledge(entries, '', '', '', '', [], 'cdk2'), entries.slice(0, 2));
  assert.deepEqual(entries, before);
});

test('scope search normalizes case and whitespace and matches substrings', () => {
  const entries = [item('one', 'CDK2   pilot\nvalidation'), item('two', 'CDK2 production')];

  assert.deepEqual(filterKnowledge(entries, '', '', '', '', [], '  cDk2\n PILOT '), [entries[0]]);
  assert.deepEqual(filterKnowledge(entries, '', '', '', '', [], 'pilot validation'), [entries[0]]);
  assert.deepEqual(filterKnowledge(entries, '', '', '', '', [], 'missing'), []);
});

test('scope search combines with content, status, and category filters', () => {
  const entries = [item('match', 'CDK2 pilot'),
    item('different-status', 'CDK2 pilot', { design_status: 'PROPOSED' }),
    item('different-category', 'CDK2 pilot', { category: 'DECISION' }),
    item('different-content', 'CDK2 pilot', { statement: 'Choose another assay.', rationale: undefined, evidence: [] }),
    item('different-scope', 'CDK9 pilot')];

  assert.deepEqual(filterKnowledge(entries, 'chemistry', 'AGREED', 'REQUIREMENT', '', [], 'cdk2'), [entries[0]]);
});

test('clearing scope search restores all records while preserving other filters', () => {
  const entries = [item('one', 'CDK2 pilot'), item('two', 'CDK9 pilot'),
    item('three', 'Other', { design_status: 'PROPOSED' })];

  assert.deepEqual(filterKnowledge(entries, '', '', '', '', [], ''), entries);
  assert.deepEqual(filterKnowledge(entries, '', '', '', '', [], ' \n '), entries);
  assert.deepEqual(filterKnowledge(entries, '', 'AGREED', '', '', [], ''), entries.slice(0, 2));
});

test('existing exact scope argument retains its exact semantics', () => {
  const entries = [item('one', 'CDK2'), item('two', 'CDK2 pilot'), item('three', 'cdk2')];

  assert.deepEqual(filterKnowledge(entries, '', '', '', 'CDK2'), [entries[0]]);
  assert.deepEqual(filterKnowledge(entries, '', '', '', 'CDK2', [], 'cdk2'), [entries[0]]);
  assert.deepEqual(filterKnowledge(entries, '', '', '', 'CDK2', [], 'pilot'), []);
});
