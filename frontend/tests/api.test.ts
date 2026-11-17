import { afterEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { api, patch, post } from '../src/api.ts';
import { completedMessages } from '../src/codex.ts';
import { chatGPTTranscriptHref, historyTurnHref, readHistoryLink, transcriptMatch } from '../src/historyLinks.ts';

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

test('history links preserve project scope and both source types', () => {
  const turn = { projectId: 27, sessionId: 4, turnId: 'turn/1' };
  const transcript = { projectId: 27, noteId: 8, query: 'cafe & tea' };
  assert.deepEqual(readHistoryLink(historyTurnHref(turn)), turn);
  assert.deepEqual(readHistoryLink(chatGPTTranscriptHref(transcript)), transcript);
  assert.equal(readHistoryLink('#history?project=0&note=8'), null);
  assert.equal(readHistoryLink('#history?project=27&note=1.5'), null);
});

test('transcript jump picks a whole matching word and preserves its accents', () => {
  const body = 'A cafeteria serves Café. Tea is also available.';
  const match = transcriptMatch(body, 'cafe tea');
  assert.ok(match);
  assert.equal(body.slice(match.start, match.start + match.length), 'Café');
  assert.equal(transcriptMatch(body, 'missing term'), null);
});

test('project requests stay scoped and need no API key', async () => {
  let request: RequestInit | undefined;
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/projects/27/overview');
    request = options;
    return Response.json({ project: { id: 27 }, questions: [] });
  };
  const value = await api('/projects/27/overview');
  assert.equal(value.project.id, 27);
  assert.equal(new Headers(request?.headers).has('Authorization'), false);
});

test('accepting an answer sends one explicit atomic action', async () => {
  const calls: string[] = [];
  globalThis.fetch = async (url, options) => {
    calls.push(String(url));
    assert.equal(options?.method, 'POST');
    assert.equal(
      new Headers(options?.headers).get('Content-Type'),
      'application/json',
    );
    assert.deepEqual(JSON.parse(String(options?.body)), {
      answer_id: 8,
      create_task: false,
    });
    return Response.json({
      decision: { id: 5 },
      question: { status: 'resolved' },
    });
  };
  const accepted = await post('/projects/2/questions/4/accept', {
    answer_id: 8,
    create_task: false,
  });
  assert.equal(accepted.question.status, 'resolved');
  assert.deepEqual(calls, ['/api/projects/2/questions/4/accept']);
});

test('decision delivery requires an explicit separate send request', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/projects/2/codex/deliveries/9/send');
    assert.deepEqual(JSON.parse(String(options?.body)), { confirmed: true });
    return Response.json({ id: 9, status: 'queued' });
  };
  assert.equal(
    (await post('/projects/2/codex/deliveries/9/send', { confirmed: true }))
      .status,
    'queued',
  );
});

test('multipart upload leaves boundary generation to the browser', async () => {
  const body = new FormData();
  body.append(
    'file',
    new Blob(['Evidence'], { type: 'text/plain' }),
    'evidence.txt',
  );
  globalThis.fetch = async (_url, options) => {
    assert.equal(options?.body, body);
    assert.equal(new Headers(options?.headers).has('Content-Type'), false);
    return Response.json({ id: 3 });
  };
  await api('/projects/2/documents', { method: 'POST', body });
});

test('record patches use PATCH and preserve caller headers', async () => {
  globalThis.fetch = async (_url, options) => {
    assert.equal(options?.method, 'PATCH');
    assert.deepEqual(JSON.parse(String(options?.body)), { status: 'blocked' });
    return Response.json({ status: 'blocked' });
  };
  assert.equal(
    (await patch('/projects/2/tasks/5', { status: 'blocked' })).status,
    'blocked',
  );
});

test('structured validation errors remain visible to the user', async () => {
  globalThis.fetch = async () =>
    Response.json(
      { detail: [{ loc: ['body', 'name'], msg: 'Required' }] },
      { status: 422 },
    );
  await assert.rejects(api('/projects'), /Required/);
});

test('non-JSON errors preserve HTTP status and network errors propagate', async () => {
  globalThis.fetch = async () =>
    new Response('Proxy unavailable', {
      status: 503,
      statusText: 'Service Unavailable',
    });
  await assert.rejects(api('/projects'), /503 Service Unavailable/);
  globalThis.fetch = async () => {
    throw new Error('Network unavailable');
  };
  await assert.rejects(api('/projects'), /Network unavailable/);
});

test('empty successful responses are supported', async () => {
  globalThis.fetch = async () => new Response(null, { status: 204 });
  assert.equal(await api('/projects/2/tasks/5'), undefined);
});

test('only completed assistant responses can be saved as proposals', () => {
  assert.deepEqual(
    completedMessages([
      {
        id: 1,
        method: 'item/agentMessage/delta',
        payload: { delta: 'Incomplete idea' },
      },
      {
        id: 2,
        method: 'item/completed',
        payload: { item: { type: 'commandExecution', text: 'Tool output' } },
      },
      {
        id: 3,
        method: 'item/completed',
        payload: {
          item: { type: 'agentMessage', text: 'Verify this proposed finding' },
        },
      },
      {
        id: 4,
        method: 'item/completed',
        payload: { item: { type: 'agentMessage', text: null } },
      },
      { id: 5, method: 'turn/completed' },
    ]),
    [{ id: 3, text: 'Verify this proposed finding' }],
  );
});
