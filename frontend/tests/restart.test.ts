import assert from 'node:assert/strict';
import test from 'node:test';
import { readRestartStatus, restartProjectHub, RestartResponseError } from '../src/restart.ts';

const status = (instance_id: string, available = true, restarting = false) =>
  ({ instance_id, available, restarting, reason: null });
const response = (body: unknown, code = 200) => new Response(JSON.stringify(body), {
  status: code, headers: { 'Content-Type': 'application/json' },
});

function harness(replies: Array<Response | Error>, timeoutMs = 5000) {
  let time = 0;
  const methods: string[] = [];
  const fetcher: typeof fetch = async (_url, init) => {
    methods.push(init?.method ?? 'GET');
    const next = replies.shift();
    if (!next) assert.fail('Unexpected extra request');
    if (next instanceof Error) throw next;
    return next;
  };
  const options = { fetch: fetcher, now: () => time, timeoutMs,
    delay: async (milliseconds: number, signal: AbortSignal) => {
      signal.throwIfAborted(); time += milliseconds;
    } };
  return { methods, options, signal: new AbortController().signal };
}

test('reachable old instance never counts as a completed restart; ready new identity does', async () => {
  const h = harness([
    response({ status: 'restarting', instance_id: 'old' }, 202),
    response(status('old')), response(status('old', true, true)),
    response(status('new', false)), response(status('new', true, true)), response(status('new')),
  ]);
  assert.equal((await restartProjectHub('old', h.signal, h.options)).instance_id, 'new');
  assert.deepEqual(h.methods, ['POST', 'GET', 'GET', 'GET', 'GET', 'GET']);
});

test('disconnect during POST or polling reconnects without sending another POST', async () => {
  const h = harness([new TypeError('connection closed'), new TypeError('offline'),
    response(status('old')), response(status('new'))]);
  assert.equal((await restartProjectHub('old', h.signal, h.options)).instance_id, 'new');
  assert.deepEqual(h.methods, ['POST', 'GET', 'GET', 'GET']);
});

test('old instance times out within the polling deadline without repeating POST', async () => {
  const h = harness([response({ status: 'restarting', instance_id: 'old' }, 202),
    response(status('old')), response(status('old'))], 2000);
  await assert.rejects(restartProjectHub('old', h.signal, h.options), /Could not confirm/);
  assert.deepEqual(h.methods, ['POST', 'GET', 'GET']);
});

test('HTTP errors preserve the server message and do not poll or retry POST', async () => {
  for (const code of [409, 503]) {
    const h = harness([response({ detail: 'AI work is active.' }, code)]);
    await assert.rejects(restartProjectHub('old', h.signal, h.options),
      error => error instanceof RestartResponseError && error.message === 'AI work is active.');
    assert.deepEqual(h.methods, ['POST']);
  }
});

test('poll HTTP errors and malformed status are explicit failures', async () => {
  const h = harness([response({ status: 'restarting', instance_id: 'old' }, 202),
    response({ detail: 'Server unavailable.' }, 503)]);
  await assert.rejects(restartProjectHub('old', h.signal, h.options), /Server unavailable/);
  await assert.rejects(readRestartStatus(h.signal, async () => response({ available: true })), /invalid restart status/);
});

test('a mismatched restart acknowledgement cannot confirm restart success', async () => {
  const h = harness([response({ status: 'restarting', instance_id: 'another' }, 202)]);
  await assert.rejects(restartProjectHub('old', h.signal, h.options), /current instance/);
  assert.deepEqual(h.methods, ['POST']);
});

test('cancellation aborts an in-flight request and sends no further requests', async () => {
  const controller = new AbortController();
  let requests = 0;
  let transportSignal: AbortSignal | null = null;
  const fetcher: typeof fetch = async (_url, init) => {
    requests++;
    if (requests === 1) return response({ status: 'restarting', instance_id: 'old' }, 202);
    transportSignal = init!.signal!;
    return new Promise((_resolve, reject) => {
      transportSignal!.addEventListener('abort', () => reject(transportSignal!.reason), { once: true });
      controller.abort();
    });
  };
  await assert.rejects(restartProjectHub('old', controller.signal, { fetch: fetcher }), /abort/i);
  assert.equal(transportSignal!.aborted, true);
  assert.equal(requests, 2);
});

test('status requests have a bounded timeout and cancellation works before POST', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let requests = 0;
  const controller = new AbortController();
  const fetcher: typeof fetch = async (_url, init) => {
    requests++;
    return new Promise((_resolve, reject) => {
      init!.signal!.addEventListener('abort', () => reject(init!.signal!.reason), { once: true });
    });
  };
  const pending = assert.rejects(readRestartStatus(controller.signal, fetcher), /abort/i);
  t.mock.timers.tick(3000);
  await pending;
  controller.abort();
  await assert.rejects(restartProjectHub('old', controller.signal, { fetch: fetcher }), /abort/i);
  assert.equal(requests, 1);
});
