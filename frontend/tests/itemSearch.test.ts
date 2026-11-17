import assert from 'node:assert/strict';
import test from 'node:test';
import { matchesItemSearch } from '../src/itemSearch.ts';

test('search matches words across title, body, and references without case sensitivity', () => {
  assert.equal(matchesItemSearch(' backup SQLITE ', ['Backup decision', 'Use SQLite', null]), true);
  assert.equal(matchesItemSearch('session-123', ['Title', 'Body', 'AI session-123']), true);
  assert.equal(matchesItemSearch('backup missing', ['Backup decision', 'Use SQLite']), false);
  assert.equal(matchesItemSearch(' ', [null]), true);
});

test('filtering finds older items before pagination and leaves source order intact', () => {
  const items = Array.from({ length: 24 }, (_, id) => ({ id, title: id === 23 ? 'Older target' : 'Other' }));
  const found = items.filter((item) => matchesItemSearch('target', [item.title])).slice(0, 10);
  assert.equal(found[0].id, 23);
  assert.equal(items.length, 24);
  assert.equal(items[0].id, 0);
});
