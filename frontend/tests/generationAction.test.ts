import assert from 'node:assert/strict';
import test from 'node:test';
import { createGenerationActionId, observeGenerationAction, parseGenerationActionStatus,
  rememberGenerationAction, restoreGenerationActions, type RememberedGenerationAction } from '../src/generationAction.ts';
import { dispatchGenerationRequest } from '../src/generationBatch.ts';
import { generationBody } from '../src/generationProvider.ts';

const active = { limit: 100, total_tokens: 60, reported_requests: 1, model_requests: 2, reached: false, active_jobs: 1 };
const idle = { ...active, active_jobs: 0 };
const finished = { ...idle, total_tokens: 120, reported_requests: 2, reached: true };
const noWait = async () => {};

test('a Generate action uses a UUID without adding it to unrelated request bodies', () => {
  const id = createGenerationActionId();
  assert.match(id, /^[a-f\d]{8}-[a-f\d]{4}-4[a-f\d]{3}-[89ab][a-f\d]{3}-[a-f\d]{12}$/);
  assert.notEqual(createGenerationActionId(), id);
  const generation = { model: 'chosen', effort: 'low' };
  assert.equal(generationBody(generation, id).generation_action_id, id);
  assert.equal(Object.hasOwn(generationBody(generation), 'generation_action_id'), false);
});

test('saved action identities survive a workspace remount and stay project-scoped', () => {
  let saved: string | null = null;
  const storage = { getItem: () => saved, setItem: (_key: string, value: string) => { saved = value; } };
  const now = 50_000_000;
  const action: RememberedGenerationAction = { id: createGenerationActionId(), kinds: ['knowledge', 'scheme'], startedAt: now };
  const other = { ...action, id: createGenerationActionId() };
  const next = { ...action, id: createGenerationActionId() };
  rememberGenerationAction(1, action, [], storage);
  rememberGenerationAction(2, other, [], storage);
  assert.deepEqual(restoreGenerationActions(1, storage, now), [action]);
  assert.deepEqual(restoreGenerationActions(2, storage, now), [other]);

  rememberGenerationAction(1, next, [action.id], storage);
  assert.deepEqual(restoreGenerationActions(1, storage, now), [action, next]);
  rememberGenerationAction(1, action, [], storage);
  assert.deepEqual(restoreGenerationActions(1, storage, now), [action]);
  assert.deepEqual(restoreGenerationActions(2, storage, now), [other]);
  assert.deepEqual(restoreGenerationActions(3, storage, now), []);
  assert.deepEqual(restoreGenerationActions(1, storage, now + 24 * 60 * 60 * 1000), []);
  saved = JSON.stringify([{ ...action, id: 'invalid/path', projectId: 1 },
    { ...action, kinds: ['unsupported'], projectId: 1 }]);
  assert.deepEqual(restoreGenerationActions(1, storage, now), []);
  saved = 'invalid JSON';
  assert.deepEqual(restoreGenerationActions(1, storage, now), []);
});

test('usage may be unavailable, but malformed counts are rejected', () => {
  assert.deepEqual(parseGenerationActionStatus({ ...active, total_tokens: null }), { ...active, total_tokens: null });
  for (const invalid of [{ active_jobs: -1 }, { total_tokens: -1 }, { reported_requests: 3 }, { limit: 1.5 }]) {
    assert.throws(() => parseGenerationActionStatus({ ...active, ...invalid }), /Invalid generation token usage/);
  }
});

test('usage polling covers temporarily missing jobs, dispatch and follow-up handoffs', async () => {
  const statuses = [null, idle, idle, idle, idle, idle, active, finished, finished, finished];
  const totals: (number | null)[] = [];
  let reads = 0;
  const outcome = await observeGenerationAction(new AbortController().signal, () => reads <= 4, async () => {
    const next = statuses[reads++];
    if (next === null) throw new Error('Action has not registered');
    assert.ok(next);
    return next;
  }, status => totals.push(status.total_tokens), noWait);
  assert.equal(outcome, 'finished');
  assert.equal(reads, statuses.length);
  assert.deepEqual(totals, [60, 60, 60, 60, 60, 60, 120, 120, 120]);
});

test('a dispatched HTTP request keeps usage polling alive before backend admission', async () => {
  const actionId = createGenerationActionId();
  let completeRequest!: () => void;
  const request = new Promise<void>(resolve => { completeRequest = resolve; });
  let requestPending = true;
  dispatchGenerationRequest('start', id => {
    assert.equal(id, actionId);
    return request;
  }, started => assert.equal(started, true), actionId, id => {
    assert.equal(id, actionId);
    requestPending = false;
  });

  const statuses = [idle, idle, idle, idle, idle, active, finished, finished, finished];
  let reads = 0;
  const totals: (number | null)[] = [];
  const outcome = await observeGenerationAction(new AbortController().signal, () => requestPending, async () => {
    const status = statuses[reads++];
    assert.ok(status);
    if (status === finished) completeRequest();
    return status;
  }, status => totals.push(status.total_tokens), noWait);
  assert.equal(outcome, 'finished');
  assert.equal(requestPending, false);
  assert.equal(reads, statuses.length);
  assert.equal(totals.at(-1), 120);
});

test('skipped and rejected dispatches settle only their own action', async () => {
  const settled: string[] = [];
  const onSettled = (id: string) => { settled.push(id); };
  dispatchGenerationRequest('skip', async () => assert.fail('Unexpected request'), undefined, 'skip-id', onSettled);
  dispatchGenerationRequest('start', async () => { throw new Error('Request failed'); }, undefined, 'failed-id', onSettled);
  assert.deepEqual(settled, ['skip-id']);
  await Promise.resolve();
  assert.deepEqual(settled, ['skip-id', 'failed-id']);
});

test('a known active job recovers after eight failed reads and reports its final total', async () => {
  const totals: (number | null)[] = [];
  const delays: number[] = [];
  let reads = 0;
  const outcome = await observeGenerationAction(new AbortController().signal, () => false, async () => {
    if (++reads === 1) return active;
    if (reads <= 9) throw new Error('Usage temporarily unavailable');
    return finished;
  }, status => totals.push(status.total_tokens), async (_signal, delay) => { delays.push(delay); });
  assert.equal(outcome, 'finished');
  assert.deepEqual(totals, [60, 120, 120, 120]);
  assert.deepEqual(delays.slice(0, 6), [1500, 3000, 6000, 10_000, 10_000, 10_000]);
  assert.deepEqual(delays.slice(-2), [1500, 1500]);
});

test('usage polling retains the last count on failures and ignores responses after unmount', async () => {
  const totals: (number | null)[] = [];
  let reads = 0;
  let outcome = await observeGenerationAction(new AbortController().signal, () => false, async () => {
    if (++reads === 1) return active;
    throw new Error('Usage temporarily unavailable');
  }, status => totals.push(status.total_tokens), noWait);
  assert.equal(outcome, 'unavailable');

  const controller = new AbortController();
  outcome = await observeGenerationAction(controller.signal, () => false, async () => {
    controller.abort();
    return finished;
  }, status => totals.push(status.total_tokens), noWait);
  assert.equal(outcome, 'aborted');
  assert.deepEqual(totals, [60]);
});
