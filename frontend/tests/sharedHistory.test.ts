import assert from 'node:assert/strict';
import test from 'node:test';
import { groupSharedHistory } from '../src/sharedHistory.ts';

const shared = (id: string) => ({ id, shared_history: { session_id: 1, turn_id: id } });
const unique = (id: string) => ({ id });

test('shared history preserves chronology across a changed live turn', () => {
  const turns = [shared('latest'), unique('changed'), shared('earliest')];
  const groups = groupSharedHistory(turns);
  assert.deepEqual(groups.map((group) =>
    group.kind === 'shared' ? group.turns.map((turn) => turn.id) : group.turn.id),
  [['latest'], 'changed', ['earliest']]);
  const flattened = groups.flatMap((group) => group.kind === 'shared' ? group.turns : [group.turn]);
  assert.deepEqual(flattened, turns);
});

test('adjacent shared turns collapse once with exact counts and original objects', () => {
  const turns = [unique('new'), shared('one'), shared('two')];
  const groups = groupSharedHistory(turns);
  assert.equal(groups.length, 2);
  assert.equal(groups[1].kind, 'shared');
  if (groups[1].kind !== 'shared') throw new Error('Expected shared group');
  assert.deepEqual(groups[1].turns, turns.slice(1));
  assert.equal(groups[1].turns[0], turns[1]);
  assert.equal(turns.length, 3);
});

test('filtering renders every matching shared turn without collapsing', () => {
  const turns = [shared('one'), shared('two')];
  assert.deepEqual(groupSharedHistory(turns, false), [
    { kind: 'turn', turn: turns[0] }, { kind: 'turn', turn: turns[1] },
  ]);
});

test('unique and empty history pages are unchanged', () => {
  assert.deepEqual(groupSharedHistory([]), []);
  const turns = [unique('one'), unique('two')];
  assert.deepEqual(groupSharedHistory(turns), turns.map((turn) => ({ kind: 'turn', turn })));
});
