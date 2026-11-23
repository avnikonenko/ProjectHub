import { afterEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { loadNote, noteRequestIdentity, validateLoadedNote } from '../src/noteLoading.ts';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
const metadata = { id: 7, project_id: 2, updated_at: 'revision-one', title: 'Saved conversation', content_loaded: false };
const complete = { id: 7, project_id: 2, updated_at: 'revision-one', title: 'Saved conversation', content: 'Exact original text.' };

test('metadata loads only the requested project-scoped note and preserves its exact content', async () => {
  const original = structuredClone(metadata);
  const abort = new AbortController();
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/projects/2/notes/7');
    assert.equal(options?.signal, abort.signal);
    assert.equal(options?.method, undefined);
    return Response.json(complete);
  };
  assert.deepEqual(await loadNote('/projects/2', metadata, abort.signal), complete);
  assert.deepEqual(metadata, original);
});

test('already-loaded note content needs no request', async () => {
  globalThis.fetch = async () => { throw new Error('Unexpected request'); };
  assert.equal(await loadNote('/projects/2', complete), complete);
});

test('transcript links validate the original identity even when note text is already loaded', async () => {
  const linkedAt = '2026-11-23T10:00:00+00:00';
  const replacement = { ...complete, created_at: '2026-11-23T11:00:00+00:00' };
  globalThis.fetch = async (url) => {
    const request = new URL(String(url), 'http://localhost');
    assert.equal(request.searchParams.get('transcript'), 'true');
    assert.equal(request.searchParams.get('created_at'), linkedAt);
    return Response.json(replacement);
  };
  await assert.rejects(loadNote('/projects/2', replacement, undefined, linkedAt), /original conversation/);
});

test('empty saved content is valid and is not replaced by invented text', async () => {
  const empty = { ...complete, content: '' };
  assert.equal(validateLoadedNote(empty, 2, 7), empty);
});

test('foreign or malformed responses cannot be displayed as the requested note', async () => {
  for (const value of [null, [], { ...complete, project_id: 3 }, { ...complete, id: 8 },
    { ...complete, content: undefined }, { ...complete, content_loaded: false }]) {
    globalThis.fetch = async () => Response.json(value);
    await assert.rejects(loadNote('/projects/2', metadata), /invalid|unrelated/);
  }
});

test('foreign metadata and invalid route identities are rejected before a request', async () => {
  globalThis.fetch = async () => { throw new Error('Unexpected request'); };
  for (const [base, note] of [
    ['/projects/3', metadata], ['/projects/2/notes', metadata], ['/projects/0', metadata],
    ['/projects/2', { ...metadata, id: -1 }], ['/projects/2', { ...metadata, id: 1.2 }],
  ] as const) await assert.rejects(loadNote(base, note), /selected project/);
});

test('aborting before a request avoids fetching', async () => {
  globalThis.fetch = async () => { throw new Error('Unexpected request'); };
  const abort = new AbortController();
  abort.abort();
  await assert.rejects(loadNote('/projects/2', metadata, abort.signal), { name: 'AbortError' });
});

test('a late completed response is rejected after cancellation even when fetch ignores abort', async () => {
  let resolve!: (value: Response) => void;
  globalThis.fetch = () => new Promise<Response>((done) => { resolve = done; });
  const abort = new AbortController();
  const request = loadNote('/projects/2', metadata, abort.signal);
  abort.abort();
  resolve(Response.json(complete));
  await assert.rejects(request, { name: 'AbortError' });
});

test('HTTP failures remain explicit instead of producing empty content', async () => {
  globalThis.fetch = async () => Response.json({ detail: 'Note not found' }, { status: 404 });
  await assert.rejects(loadNote('/projects/2', metadata), /Note not found/);
});

test('request identities change with project, note, or revision', () => {
  const original = noteRequestIdentity('/projects/2', metadata);
  for (const [base, note] of [
    ['/projects/3', { ...metadata, project_id: 3 }], ['/projects/2', { ...metadata, id: 8 }],
    ['/projects/2', { ...metadata, updated_at: 'revision-two' }],
  ] as const) assert.notEqual(noteRequestIdentity(base, note), original);
});
