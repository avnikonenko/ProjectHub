import assert from 'node:assert/strict';
import test from 'node:test';
import { filterKnowledge, importantKnowledge, knowledgePassageGroups, visibleKnowledgeGroups,
  type KnowledgeItem, type KnowledgeSnapshot } from '../src/knowledge.ts';
const revision = 'b5a50449f253f69e0b422f8f32227daa7ff90949b4d10135e9068b6b4fa45a28';
const preference = (id: string, changes: Partial<KnowledgeItem> = {}): KnowledgeItem => ({
  id, project_id: 2, statement: 'Prefer validated tools for the preparation workflow.',
  category: 'REQUIREMENT', design_status: 'AGREED', scope: 'Tool selection',
  agreement_basis: 'direct_user', source_state: 'valid', evidence: [
    { source_id: 'chatgpt:note:5:m:7:p:1', role: 'user', excerpt: 'can we rather use validated tools?', revision },
  ], ...changes,
});
const snapshot = (items: KnowledgeItem[]): KnowledgeSnapshot => ({ project_id: 2, items, sources: [] });
const pair = () => [preference('a'), preference('b', {
  statement: 'Use scientifically validated tools for Example Research.',
  evidence: [...preference('a').evidence,
    { source_id: 'heading', role: 'assistant', excerpt: 'Choosing scientifically validated tools', revision }],
})];
test('same-passage preference display preserves all original statements, IDs and evidence', () => {
  const data = snapshot(pair()); const before = structuredClone(data);
  const groups = knowledgePassageGroups(data);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].primary, data.items[0]);
  assert.deepEqual(groups[0].related, [data.items[1]]);
  assert.deepEqual(data, before);
  assert.deepEqual(importantKnowledge(data).map(item => item.id), ['a']);
  assert.equal(filterKnowledge(data.items, 'validated tools', 'AGREED', 'REQUIREMENT', 'Tool selection').length, 2);
  const onlySecond = filterKnowledge(data.items, 'scientifically');
  assert.equal(knowledgePassageGroups(data, onlySecond)[0].primary.id, 'b');
});
test('different statuses, scopes, categories, projects and agreement bases remain independent', () => {
  for (const change of [
    { design_status: 'PROPOSED' }, { scope: 'First release' }, { category: 'DECISION' },
    { project_id: 3 }, { agreement_basis: 'proposal_acceptance' }, { agreement_basis: undefined },
    { source_state: 'stale' }, { source_state: 'unavailable' }, { reconciliation_state: 'unresolved' },
    { conflicts_with: ['other'] }, { supersedes: ['other'] }, { superseded_by: ['other'] },
    { relationships: [{ kind: 'conflict', target: 'other' }] },
  ]) {
    const data = snapshot([preference('a'), preference('b', change)]);
    assert.equal(knowledgePassageGroups(data).length, 2, JSON.stringify(change));
  }
  const data = snapshot(pair());
  assert.equal(knowledgePassageGroups(data, data.items, { b: 'stale' }).length, 2);
});
test('incoming relationships and invalid or different user evidence prevent grouping', () => {
  for (const change of [
    { revision: undefined }, { revision: '' }, { revision: 'invalid' }, { revision: 'a'.repeat(64) },
    { source_id: 'different' }, { excerpt: 'Can we rather use validated tools?' },
  ]) {
    const data = snapshot([preference('a'), preference('b', {
      evidence: [{ ...preference('a').evidence[0], ...change }],
    })]);
    assert.equal(knowledgePassageGroups(data).length, 2, JSON.stringify(change));
  }
  for (const change of [{ conflicts_with: ['a'] }, { supersedes: ['a'] }, { superseded_by: ['a'] }]) {
    const data = snapshot([...pair(), preference('c', change)]);
    assert.equal(knowledgePassageGroups(data).length, 3);
  }
  const data = snapshot(pair().map(item => ({ ...item, agreement_basis: 'proposal_acceptance' })));
  assert.equal(knowledgePassageGroups(data).length, 2);
});
test('direct links to related children reveal their group beyond pagination with child identity intact', () => {
  const data = snapshot([preference('0', { scope: 'Other' }), ...pair()]);
  const groups = knowledgePassageGroups(data);
  const shown = visibleKnowledgeGroups(groups, 1, 'b');
  assert.equal(shown.length, 2);
  assert.equal(shown[1].related[0].id, 'b');
  assert.equal(visibleKnowledgeGroups(groups, 1).length, 1);
});
test('distinct claims from one passage remain available as separate interpretations', () => {
  const data = snapshot([preference('a'), preference('b', { statement: 'Preserve documented tool versions.' })]);
  const groups = knowledgePassageGroups(data);
  assert.equal(groups[0].related[0].statement, 'Preserve documented tool versions.');
  assert.equal(data.items.length, 2);
});
