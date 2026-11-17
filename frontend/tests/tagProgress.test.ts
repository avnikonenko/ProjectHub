import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createTagProgressMonitor, type TagProgressState } from '../src/tagProgress.ts';
import type { Progress } from '../src/GenerationStatus';

const settle = () => new Promise<void>(resolve => setImmediate(resolve));
const status = (state: Progress['state']): Progress => ({ state, stage: '', sessions: 0 });

test('multiple tag widgets share one recovery read and idle does not continue polling', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let calls = 0;
  const monitor = createTagProgressMonitor(async () => { calls++; return status('idle'); }, 5);
  let first!: TagProgressState, second!: TagProgressState;
  const a = monitor.subscribe('/projects/1', value => { first = value; });
  const b = monitor.subscribe('/projects/1', value => { second = value; });
  await settle();
  t.mock.timers.tick(25);
  assert.equal(calls, 1);
  assert.equal(first.progress, second.progress);
  a(); b();
});

test('active recovery polls serially and stops at completion', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const requests: ((progress: Progress) => void)[] = [];
  const monitor = createTagProgressMonitor(() => new Promise(resolve => { requests.push(resolve); }), 2);
  let last!: TagProgressState;
  const release = monitor.subscribe('/projects/1', value => { last = value; });
  try {
    for (const index of [0, 1]) {
      assert.equal(requests.length, index + 1);
      t.mock.timers.tick(20);
      assert.equal(requests.length, index + 1, 'a pending read must not overlap another poll');
      requests[index](status('running'));
      await settle();
      assert.equal(last.progress?.state, 'running');
      t.mock.timers.tick(1);
      assert.equal(requests.length, index + 1, 'the delay starts when the read settles');
      t.mock.timers.tick(1);
    }
    assert.equal(requests.length, 3);
    requests[2](status('completed'));
    await settle();
    assert.equal(last.progress?.state, 'completed');
    t.mock.timers.tick(20);
    assert.equal(requests.length, 3, 'completion must stop polling');
  } finally {
    release();
  }
});

test('local generation intent keeps recovery alive across an initial old terminal response', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let calls = 0;
  const monitor = createTagProgressMonitor(async () => { calls++; return status('completed'); }, 3);
  let last!: TagProgressState;
  const release = monitor.subscribe('/projects/1', value => { last = value; });
  monitor.begin('/projects/1');
  await settle();
  t.mock.timers.tick(3);
  await settle();
  assert.equal(calls, 2);
  assert.equal(last.starting, true);
  monitor.end('/projects/1');
  await settle();
  const completedCalls = calls;
  t.mock.timers.tick(12);
  assert.equal(calls, completedCalls);
  assert.equal(last.starting, false);
  release();
});

test('idle recovery errors stop until explicit retry and successful recovery clears the error', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let calls = 0;
  const monitor = createTagProgressMonitor(async () => {
    calls++;
    if (calls === 1) throw new Error('temporarily unavailable');
    return status('idle');
  }, 3);
  let last!: TagProgressState;
  const release = monitor.subscribe('/projects/1', value => { last = value; });
  await settle();
  t.mock.timers.tick(15);
  assert.equal(calls, 1);
  assert.match(last.error, /unavailable/);
  await monitor.refresh('/projects/1');
  assert.equal(calls, 2);
  assert.equal(last.error, '');
  release();
});

test('a terminal progress update clears the old timer so later readers can recover fresh status', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1000 });
  let calls = 0;
  const monitor = createTagProgressMonitor(async () => {
    calls++;
    return status(calls === 1 ? 'running' : 'idle');
  }, 5);
  const first = monitor.subscribe('/projects/1', () => {});
  await monitor.refresh('/projects/1');
  assert.equal(calls, 1);
  monitor.update('/projects/1', status('completed'));
  t.mock.timers.tick(12);
  assert.equal(calls, 1, 'the cancelled timer must not poll');
  const second = monitor.subscribe('/projects/1', () => {});
  assert.equal(calls, 2, 'a stale timer handle must not suppress this recovery read');
  first(); second();
  await Promise.resolve();
});

test('unmount aborts pending reads, suppresses late responses and does not retain another project', async () => {
  let finish!: (value: Progress) => void;
  let signal!: AbortSignal;
  const monitor = createTagProgressMonitor((_base, token) => {
    signal = token;
    return new Promise(resolve => { finish = resolve; });
  }, 3);
  let updates = 0;
  const release = monitor.subscribe('/projects/1', () => { updates++; });
  release();
  await Promise.resolve();
  assert.equal(signal.aborted, true);
  finish(status('running'));
  await settle();
  assert.equal(updates, 1);
  let fresh!: TagProgressState;
  const other = monitor.subscribe('/projects/2', value => { fresh = value; });
  assert.equal(fresh.progress, null);
  other();
  await Promise.resolve();
  finish(status('idle'));
});
