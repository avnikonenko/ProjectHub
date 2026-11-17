import assert from 'node:assert/strict';
import test from 'node:test';
import { knowledgePreviewExport, recoverKnowledgePreview } from '../src/knowledgePreviewRecovery.ts';
import { parseKnowledgePreview } from '../src/knowledge.ts';

function preview(publication = 'preview-1', project = 2) {
  return { published: false, publication_id: publication,
    knowledge: { project_id: project, publication_id: publication, items: [] as Record<string, unknown>[], sources: [] },
    brief: { generated_at: '2026-11-14T00:00:00Z', project_state: { publication_id: publication },
      sections: { overview: [], current_status: [], architecture: [] }, sources: [], coverage: [] } };
}

test('saved unpublished preview recovers with project-scoped GETs and no generation', async () => {
  const calls: string[] = [], events: any[] = [];
  const raw = preview(); const before = structuredClone(raw);
  await recoverKnowledgePreview('/projects/2', 2, new AbortController().signal, (event) => events.push(event),
    async (path) => { calls.push(path); return path.endsWith('/preview') ? raw : { state: 'completed' }; });
  assert.deepEqual(calls.sort(), ['/projects/2/codex/generation/status?kind=knowledge',
    '/projects/2/codex/analysis/preview', '/projects/2/codex/analysis/preview'].sort());
  assert.equal(events.find((event) => event.type === 'saved').result.knowledge.publication_id, 'preview-1');
  assert.deepEqual(raw, before);
});

test('completion during the initial saved read reloads the newly committed preview', async () => {
  const events: any[] = []; let reads = 0;
  await recoverKnowledgePreview('/projects/2', 2, new AbortController().signal, (event) => events.push(event),
    async (path) => path.endsWith('/preview') ? preview(++reads === 1 ? 'old' : 'new') : { state: 'completed' });
  assert.equal(reads, 2);
  assert.equal(events.filter((event) => event.type === 'saved').at(-1).result.knowledge.publication_id, 'new');
});

test('refresh during generation keeps previous preview and retrieves completed replacement', async () => {
  const events: any[] = [], states = ['running', 'running', 'completed'];
  let savedCalls = 0, waits = 0;
  await recoverKnowledgePreview('/projects/2', 2, new AbortController().signal, (event) => events.push(event),
    async (path) => path.endsWith('/preview') ? preview(`preview-${++savedCalls}`) : { state: states.shift() },
    async () => { waits++; });
  const saved = events.filter((event) => event.type === 'saved');
  assert.deepEqual(saved.map((event) => event.result.knowledge.publication_id), ['preview-1', 'preview-2']);
  assert.deepEqual(events.filter((event) => event.type === 'progress').map((event) => event.progress.state), ['running', 'running', 'completed']);
  assert.equal(waits, 2);
});

test('failed or cancelled replacement does not clear the previous successful preview', async () => {
  for (const terminal of ['failed', 'cancelled', 'idle']) {
    const events: any[] = []; const states = ['running', terminal];
    let savedCalls = 0;
    await recoverKnowledgePreview('/projects/2', 2, new AbortController().signal, (event) => events.push(event),
      async (path) => path.endsWith('/preview') ? (++savedCalls, preview()) : { state: states.shift(), error: terminal === 'failed' ? 'Failure' : null },
      async () => {});
    assert.equal(savedCalls, 1);
    assert.equal(events.filter((event) => event.type === 'saved')[0].result.knowledge.publication_id, 'preview-1');
    assert.equal(events.at(-1).progress.state, terminal);
  }
});

test('failed final saved read still reports completed progress and retains previous preview', async () => {
  for (const initial of ['running', 'completed']) {
    const events: any[] = []; const states = [initial, 'completed']; let reads = 0;
    await assert.rejects(() => recoverKnowledgePreview('/projects/2', 2, new AbortController().signal,
      (event) => events.push(event), async (path) => {
        if (!path.endsWith('/preview')) return { state: states.shift() };
        if (++reads === 1) return preview();
        throw new Error('Final saved read unavailable');
      }, async () => {}), /Final saved read unavailable/);
    assert.equal(events.at(-1).progress.state, 'completed');
    assert.equal(events.filter((event) => event.type === 'saved').length, 1);
    assert.equal(events.find((event) => event.type === 'saved').result.knowledge.publication_id, 'preview-1');
  }
});

test('intermediate status failure rejects recovery without clearing its saved preview', async () => {
  const events: any[] = []; let statuses = 0;
  await assert.rejects(() => recoverKnowledgePreview('/projects/2', 2, new AbortController().signal,
    (event) => events.push(event), async (path) => {
      if (path.endsWith('/preview')) return preview();
      if (++statuses === 1) return { state: 'running' };
      throw new Error('Progress unavailable');
    }, async () => {}), /Progress unavailable/);
  assert.equal(events.find((event) => event.type === 'saved').result.knowledge.publication_id, 'preview-1');
  assert.deepEqual(events.filter((event) => event.type === 'progress').map((event) => event.progress.state), ['running']);
});

test('absent preview remains unpublished and malformed or foreign data is rejected', async () => {
  const events: any[] = [];
  const signal = new AbortController().signal;
  await recoverKnowledgePreview('/projects/2', 2, signal, (event) => events.push(event),
    async (path) => path.endsWith('/preview') ? null : { state: 'idle' });
  assert.equal(events.find((event) => event.type === 'saved').result, null);
  await assert.rejects(() => recoverKnowledgePreview('/projects/2', 2, signal, () => {},
    async (path) => path.endsWith('/preview') ? preview('foreign', 3) : { state: 'idle' }), /project/);
  await assert.rejects(() => recoverKnowledgePreview('/projects/2', 2, signal, () => {},
    async (path) => path.endsWith('/preview') ? null : { state: 'invented' }), /generation status/);
});

test('aborted project requests cannot publish stale recovery state', async () => {
  const controller = new AbortController(); const events: any[] = [];
  let savedResolve!: (value: unknown) => void;
  const result = recoverKnowledgePreview('/projects/2', 2, controller.signal, (event) => events.push(event),
    async (path) => path.endsWith('/preview') ? new Promise((resolve) => { savedResolve = resolve; }) : { state: 'idle' });
  controller.abort(); savedResolve(preview());
  await result;
  assert.deepEqual(events, []);
});

test('starting local generation invalidates late recovery even when its request ignores abort', async () => {
  const controller = new AbortController(); let release!: (value: unknown) => void;
  const events: any[] = [];
  const recovery = recoverKnowledgePreview('/projects/2', 2, controller.signal, (event) => events.push(event),
    async (path) => path.endsWith('/preview') ? new Promise((resolve) => { release = resolve; }) : { state: 'completed' });
  // KnowledgePage aborts this controller before starting its explicit POST.
  controller.abort();
  const localResult = parseKnowledgePreview(preview('local-post-result'), 2);
  release(preview('older-get-result'));
  await recovery;
  assert.deepEqual(events, []);
  assert.equal(localResult.knowledge.publication_id, 'local-post-result');
});

test('aborting recovered polling stops further reads and terminal notifications', async () => {
  const controller = new AbortController(); const events: any[] = []; const calls: string[] = [];
  await recoverKnowledgePreview('/projects/2', 2, controller.signal, (event) => events.push(event),
    async (path) => { calls.push(path); return path.endsWith('/preview') ? preview() : { state: 'running' }; },
    async () => { controller.abort(); });
  assert.equal(calls.length, 2);
  assert.equal(events.filter((event) => event.type === 'progress').length, 1);
});

test('JSON export preserves exact evidence and paired unpublished identities', () => {
  const raw = preview();
  raw.knowledge.items = [{ id: 'preserved', project_id: 2, statement: 'Keep the original quotation.',
    category: 'PROPOSAL', design_status: 'PROPOSED', scope: 'source preservation',
    evidence: [{ source_id: 'original', role: 'user', excerpt: 'Exact quotation: Zn²⁺.\nKeep spacing.' }] }];
  const parsed = parseKnowledgePreview(raw, 2); const before = structuredClone(parsed);
  const exported = knowledgePreviewExport(parsed, 2);
  assert.equal(exported.filename, 'project-2-knowledge-preview.json');
  assert.deepEqual(parseKnowledgePreview(JSON.parse(exported.text), 2), parsed);
  assert.equal(JSON.parse(exported.text).published, false);
  assert.deepEqual(parsed, before);
  assert.throws(() => knowledgePreviewExport(parsed, 3), /another project/);
});

test('lost local POST waits through old idle status and follows the new run without generation requests', async () => {
  const states = [{ state: 'idle' }, { state: 'running', started_at: 'new-run' },
    { state: 'completed', started_at: 'new-run' }];
  const events: any[] = [], calls: string[] = [];
  let waits = 0, reads = 0;
  await recoverKnowledgePreview('/projects/2', 2, new AbortController().signal, event => events.push(event),
    async path => { calls.push(path); return path.endsWith('/preview') ? preview(++reads === 1 ? 'old' : 'new') : states.shift(); },
    async () => { waits++; }, {});
  assert.equal(waits, 2);
  assert.equal(events.filter(event => event.type === 'saved').at(-1).result.knowledge.publication_id, 'new');
  assert.deepEqual(events.filter(event => event.type === 'progress').map(event => event.progress.state), ['running', 'completed']);
  assert.ok(calls.every(path => path.endsWith('/analysis/preview') || path.endsWith('/generation/status?kind=knowledge')));
});

test('lost already-completed POST ignores the prior run and rereads the replacement', async () => {
  const events: any[] = [], states = [{ state: 'completed', started_at: 'prior' }, { state: 'completed', started_at: 'new' }];
  let reads = 0, waits = 0;
  await recoverKnowledgePreview('/projects/2', 2, new AbortController().signal, event => events.push(event),
    async path => path.endsWith('/preview') ? preview(++reads === 1 ? 'old' : 'new') : states.shift(),
    async () => { waits++; }, { previousStartedAt: 'prior' });
  assert.equal(waits, 1);
  assert.equal(reads, 2);
  assert.equal(events.at(-1).progress.started_at, 'new');
  assert.equal(events.filter(event => event.type === 'saved').at(-1).result.knowledge.publication_id, 'new');
});

test('local POST handoff is bounded and aborting it prevents further reads', async () => {
  let statuses = 0, waits = 0;
  const events: any[] = [];
  await recoverKnowledgePreview('/projects/2', 2, new AbortController().signal, event => events.push(event),
    async path => path.endsWith('/preview') ? preview() : (++statuses, { state: 'idle' }),
    async () => { waits++; }, {});
  assert.equal(waits, 2); assert.equal(statuses, 3);
  assert.equal(events.at(-1).progress.state, 'idle');
  const controller = new AbortController(); statuses = 0;
  const aborted: any[] = [];
  await recoverKnowledgePreview('/projects/2', 2, controller.signal, event => aborted.push(event),
    async path => path.endsWith('/preview') ? preview() : (++statuses, { state: 'idle' }),
    async () => { controller.abort(); }, {});
  assert.equal(statuses, 1);
  assert.deepEqual(aborted.map(event => event.type), ['saved']);
});
