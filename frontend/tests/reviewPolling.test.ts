import assert from 'node:assert/strict';
import test from 'node:test';
import { startReviewPolling } from '../src/reviewPolling.ts';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

test('review polling never overlaps slow requests and delays from completion', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const requests: ReturnType<typeof deferred<number>>[] = [];
  const results: number[] = [];
  const stop = startReviewPolling(() => {
    const request = deferred<number>(); requests.push(request); return request.promise;
  }, value => results.push(value), () => assert.fail('unexpected error'));
  try {
    assert.equal(requests.length, 1);
    t.mock.timers.tick(9000);
    assert.equal(requests.length, 1);
    requests[0].resolve(1); await flush();
    assert.deepEqual(results, [1]);
    t.mock.timers.tick(2999); assert.equal(requests.length, 1);
    t.mock.timers.tick(1); assert.equal(requests.length, 2);
    requests[1].resolve(2); await flush();
    assert.deepEqual(results, [1, 2]);
  } finally { stop(); }
});

test('cleanup aborts an active request and suppresses its late result', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const request = deferred<number>(); let signal!: AbortSignal; let loads = 0;
  const stop = startReviewPolling(value => {
    signal = value; loads += 1; return request.promise;
  }, () => assert.fail('late result'), () => assert.fail('late error'));
  stop(); stop();
  assert.equal(signal.aborted, true);
  request.resolve(1); await flush();
  t.mock.timers.tick(10000);
  assert.equal(loads, 1);
});

test('cleanup clears a scheduled retry and ignores late rejection', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let loads = 0;
  const stop = startReviewPolling(async () => { loads += 1; return 1; }, () => {}, () => {});
  await flush(); stop(); t.mock.timers.tick(10000);
  assert.equal(loads, 1);
  const request = deferred<number>();
  const stopPending = startReviewPolling(() => request.promise,
    () => assert.fail('late result'), () => assert.fail('late error'));
  stopPending(); request.reject(new Error('aborted transport')); await flush();
});

test('load failure is reported and retried without overlap', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let loads = 0; const errors: unknown[] = []; const results: number[] = [];
  const failure = new Error('temporary disconnect');
  const stop = startReviewPolling(async () => {
    loads += 1; if (loads === 1) throw failure; return loads;
  }, value => results.push(value), error => errors.push(error), 100);
  try {
    await flush(); assert.deepEqual(errors, [failure]);
    t.mock.timers.tick(100); await flush();
    assert.equal(loads, 2); assert.deepEqual(results, [2]);
  } finally { stop(); }
});
