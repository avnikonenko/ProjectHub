import assert from 'node:assert/strict';
import test from 'node:test';
import { completedMessages } from '../src/codex.ts';

test('completed messages retain recorded timestamps without changing text or order', () => {
  const created_at = '2026-11-16T10:15:30+00:00';
  const events = [
    { id: 7, method: 'item/completed', created_at, payload: { item: { type: 'agentMessage', text: 'Answer' } } },
    { id: 8, method: 'item/completed', payload: { item: { type: 'agentMessage', text: 'Older record' } } },
  ];
  const before = structuredClone(events);
  assert.deepEqual(completedMessages(events), [
    { id: 7, text: 'Answer', created_at }, { id: 8, text: 'Older record' },
  ]);
  assert.deepEqual(events, before);
});

test('missing and invalid timestamps are omitted rather than invented', () => {
  for (const created_at of [undefined, null, '', 'not a date', 123]) {
    assert.deepEqual(completedMessages([
      { id: 1, method: 'item/completed', created_at, payload: { item: { type: 'agentMessage', text: 'Answer' } } },
    ]), [{ id: 1, text: 'Answer' }]);
  }
});

test('timestamps do not turn deltas, tool output or malformed items into completed answers', () => {
  const created_at = '2026-11-16T10:15:30Z';
  assert.deepEqual(completedMessages([
    { id: 1, method: 'item/agentMessage/delta', created_at, payload: { item: { type: 'agentMessage', text: 'Partial' } } },
    { id: 2, method: 'item/completed', created_at, payload: { item: { type: 'commandExecution', text: 'Output' } } },
    { id: 3, method: 'item/completed', created_at, payload: { item: { type: 'agentMessage', text: 42 } } },
    { id: 4, method: 'item/completed', created_at },
  ]), []);
});

test('saved questions remain attached only to their completed response', () => {
  const event = { id: 1, method: 'item/completed', question: 'Why this approach?',
    payload: { item: { type: 'agentMessage', text: 'Because it preserves history.' } } };
  assert.deepEqual(completedMessages([event]), [
    { id: 1, text: 'Because it preserves history.', question: 'Why this approach?' },
  ]);
  assert.deepEqual(completedMessages([{ ...event, question: null }]), [
    { id: 1, text: 'Because it preserves history.' },
  ]);
});
