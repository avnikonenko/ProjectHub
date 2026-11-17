import assert from 'node:assert/strict';
import test from 'node:test';
import { ALL_TOPICS, OTHER_TOPICS, filterTopicItems, parseTopicGroups, topicChoices } from '../src/topicGroups.ts';
const items = [{ id: 'a', status: 'AGREED' }, { id: 'b', status: 'PROPOSED' }, { id: 'c', status: 'AGREED' }];
const groups = [
  { id: 'one', label: 'First', description: 'First topic', item_ids: ['a', 'b', 'a', 'missing'] },
  { id: 'two', label: 'Second', description: 'Second topic', item_ids: ['b'] },
];
const id = (item: typeof items[number]) => item.id;
test('topics overlap, count unique records and preserve original records', () => {
  const before = structuredClone(items);
  const choices = topicChoices(items, groups, id, item => item.status);
  assert.deepEqual(choices.map(group => [group.id, group.items.length]), [[ALL_TOPICS, 3], ['one', 2], ['two', 1], [OTHER_TOPICS, 1]]);
  assert.deepEqual(choices[1].statuses, { AGREED: 1, PROPOSED: 1 });
  assert.equal(choices[1].items[1], items[1]);
  assert.deepEqual(items, before);
});
test('All, Other and legacy snapshots retain every item', () => {
  const choices = topicChoices(items, undefined, id);
  assert.deepEqual(filterTopicItems(items, ALL_TOPICS, choices, id), items);
  assert.deepEqual(filterTopicItems(items, OTHER_TOPICS, choices, id), items);
  assert.deepEqual(filterTopicItems(items, 'unavailable', choices, id), items);
});
test('invalid group metadata and foreign references cannot add records', () => {
  assert.deepEqual(parseTopicGroups([null, {}, { ...groups[0], item_ids: [7] },
    { ...groups[0], id: ALL_TOPICS }, { ...groups[0], item_ids: ['foreign'] }], items.map(id)), []);
  assert.equal(parseTopicGroups([...groups, groups[0]], items.map(id)).length, 2);
});
test('topic membership intersects filters without reordering or restoring filtered records', () => {
  const choices = topicChoices(items, groups, id);
  const filtered = items.filter(item => item.status === 'PROPOSED');
  assert.deepEqual(filterTopicItems(filtered, 'one', choices, id), [items[1]]);
  assert.deepEqual(filterTopicItems(filtered, OTHER_TOPICS, choices, id), []);
  assert.deepEqual(filterTopicItems(items, OTHER_TOPICS, choices, id), [items[2]]);
});

test('status labels from untrusted data remain ordinary count keys', () => {
  const choices = topicChoices([{ id: 'a', status: '__proto__' }, { id: 'b', status: 'constructor' }], [], id, item => item.status);
  assert.equal(choices[0].statuses.__proto__, 1);
  assert.equal(choices[0].statuses.constructor, 1);
  assert.equal(Object.getPrototypeOf(choices[0].statuses), Object.prototype);
});

test('knowledge topic parsing keeps each publication separate and rejects foreign snapshots', async () => {
  const { parseKnowledgeResponse } = await import('../src/knowledge.ts');
  const record = { id: 'a', statement: 'Statement', category: 'DECISION', design_status: 'AGREED', scope: 'Project', evidence: [] };
  const snapshot = { project_id: 2, publication_id: 'published', items: [record], sources: [], topic_groups: groups };
  const before = structuredClone(snapshot);
  const published = parseKnowledgeResponse({ snapshot }, 2).snapshot!;
  const preview = parseKnowledgeResponse({ snapshot: { ...snapshot, publication_id: 'preview', topic_groups: [] } }, 2).snapshot!;
  assert.equal(published.topic_groups?.length, 1);
  assert.deepEqual(published.topic_groups?.[0].item_ids, ['a']);
  assert.deepEqual(preview.topic_groups, []);
  assert.equal(published.items[0], record);
  assert.deepEqual(snapshot, before);
  assert.throws(() => parseKnowledgeResponse({ snapshot }, 3));
});

test('typing a tag filters records by matching tag labels and combines memberships once', () => {
  const choices = topicChoices(items, groups, id);
  assert.deepEqual(filterTopicItems(items, ALL_TOPICS, choices, id, ' FIRST '), items.slice(0, 2));
  assert.deepEqual(filterTopicItems(items, ALL_TOPICS, choices, id, 's'), items.slice(0, 2));
  assert.deepEqual(filterTopicItems(items, ALL_TOPICS, choices, id, 'missing'), []);
  assert.deepEqual(filterTopicItems([items[1]], ALL_TOPICS, choices, id, 'first'), [items[1]]);
});
