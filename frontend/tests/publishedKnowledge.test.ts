import { afterEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readPublishedKnowledge, subscribePublishedKnowledge, updatePublishedKnowledge,
  type PublishedKnowledgeState } from '../src/publishedKnowledge.ts';

const originalFetch = globalThis.fetch;
const originalNow = Date.now;
const releases: (() => void)[] = [];
const response = (project = 27, publication = 'one') => ({
  snapshot: { project_id: project, publication_id: publication, items: [], sources: [] },
  current_evidence_availability: {}, coverage: {},
});
afterEach(async () => {
  releases.splice(0).forEach(release => release());
  await Promise.resolve();
  globalThis.fetch = originalFetch;
  Date.now = originalNow;
});

test('Overview and Knowledge share one in-flight request and canonical parsed snapshot', async () => {
  let finish!: (response: Response) => void;
  let calls = 0;
  globalThis.fetch = () => { calls++; return new Promise(resolve => { finish = resolve; }); };
  let first!: PublishedKnowledgeState, second!: PublishedKnowledgeState;
  releases.push(subscribePublishedKnowledge('/projects/27', state => { first = state; }));
  releases.push(subscribePublishedKnowledge('/projects/27', state => { second = state; }));
  const a = readPublishedKnowledge('/projects/27', { publicationId: 'one' });
  const b = readPublishedKnowledge('/projects/27', { publicationId: 'one' });
  assert.equal(calls, 1);
  finish(Response.json(response()));
  await Promise.all([a, b]);
  assert.equal(first.data, second.data);
  assert.equal(first.data?.snapshot, second.data?.snapshot);
  await readPublishedKnowledge('/projects/27');
  assert.equal(calls, 1);
});

test('explicit refresh updates every reader while failed refresh preserves the last valid snapshot', async () => {
  let count = 0;
  globalThis.fetch = async () => {
    count++;
    if (count === 3) throw new Error('read unavailable');
    return Response.json(response(27, count === 1 ? 'one' : 'two'));
  };
  let state!: PublishedKnowledgeState;
  releases.push(subscribePublishedKnowledge('/projects/27', value => { state = value; }));
  await readPublishedKnowledge('/projects/27');
  await readPublishedKnowledge('/projects/27', { refresh: true });
  const saved = state.data;
  assert.equal(saved?.snapshot?.publication_id, 'two');
  await readPublishedKnowledge('/projects/27', { refresh: true });
  assert.equal(state.data, saved);
  assert.match(state.error, /unavailable/);
  assert.equal(state.loading, false);
});

test('returning to a mounted view revalidates older cached publication and evidence state', async () => {
  let now = 10000, calls = 0;
  Date.now = () => now;
  globalThis.fetch = async () => { calls++; return Response.json(response(27, calls === 1 ? 'one' : 'two')); };
  let state!: PublishedKnowledgeState;
  releases.push(subscribePublishedKnowledge('/projects/27', value => { state = value; }));
  await readPublishedKnowledge('/projects/27');
  await readPublishedKnowledge('/projects/27');
  assert.equal(calls, 1);
  now += 1600;
  await readPublishedKnowledge('/projects/27');
  assert.equal(calls, 2);
  assert.equal(state.data?.snapshot?.publication_id, 'two');
});

test('publication change during an in-flight request gets one follow-up read, not an unlimited retry', async () => {
  let finish!: (response: Response) => void;
  let calls = 0;
  globalThis.fetch = () => {
    calls++;
    return calls === 1 ? new Promise(resolve => { finish = resolve; }) : Promise.resolve(Response.json(response(27, 'two')));
  };
  let state!: PublishedKnowledgeState;
  releases.push(subscribePublishedKnowledge('/projects/27', value => { state = value; }));
  const old = readPublishedKnowledge('/projects/27', { publicationId: 'one' });
  const changed = readPublishedKnowledge('/projects/27', { publicationId: 'two' });
  finish(Response.json(response(27, 'one')));
  await Promise.all([old, changed]);
  assert.equal(calls, 2);
  assert.equal(state.data?.snapshot?.publication_id, 'two');
  await readPublishedKnowledge('/projects/27', { publicationId: 'unavailable-history' });
  assert.equal(calls, 3);
  assert.equal(state.data?.snapshot?.publication_id, 'two');
});

test('reader cleanup aborts the request and releases project data before the next mount', async () => {
  let signal!: AbortSignal;
  let finish!: (response: Response) => void;
  globalThis.fetch = (_url, options) => {
    signal = options!.signal as AbortSignal;
    return new Promise(resolve => { finish = resolve; });
  };
  const release = subscribePublishedKnowledge('/projects/27', () => {});
  const pending = readPublishedKnowledge('/projects/27');
  release();
  await Promise.resolve();
  assert.equal(signal.aborted, true);
  finish(Response.json(response()));
  await pending;
  let state!: PublishedKnowledgeState;
  releases.push(subscribePublishedKnowledge('/projects/27', value => { state = value; }));
  assert.equal(state.data, null);
});

test('publishing a new result cancels stale reads and project ownership remains checked', async () => {
  let finish!: (response: Response) => void;
  globalThis.fetch = () => new Promise(resolve => { finish = resolve; });
  let state!: PublishedKnowledgeState;
  releases.push(subscribePublishedKnowledge('/projects/27', value => { state = value; }));
  const pending = readPublishedKnowledge('/projects/27');
  updatePublishedKnowledge('/projects/27', () => response(27, 'published'));
  finish(Response.json(response(27, 'old')));
  await pending;
  assert.equal(state.data?.snapshot?.publication_id, 'published');
  globalThis.fetch = async () => Response.json(response(28));
  await readPublishedKnowledge('/projects/27', { refresh: true });
  assert.match(state.error, /project|malformed/);
  assert.equal(state.data?.snapshot?.publication_id, 'published');
});
