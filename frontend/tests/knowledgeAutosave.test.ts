import assert from 'node:assert/strict';
import test from 'node:test';
import { parseKnowledgePreview, parseKnowledgeResponse, parsePublishedKnowledgeResult } from '../src/knowledge.ts';
import { generateAndSaveKnowledge, recoverPublishedKnowledge, savedKnowledgeMatchesRun } from '../src/knowledgePreviewRecovery.ts';

const started = '2026-11-17T12:00:00.000Z';
function published(publication = 'saved-new', project = 2, generated = '2026-11-17T12:01:00.000Z') {
  const brief = { generated_at: generated, project_state: { publication_id: publication },
    sections: { overview: [], current_status: [], architecture: [] }, sources: [], coverage: [] };
  return { published: true, publication_id: publication, scheme_requested: true,
    knowledge: { project_id: project, publication_id: publication, generated_at: generated,
      items: [], sources: [], summary: brief }, brief };
}
const response = (value = published()) => ({ snapshot: value.knowledge, current_evidence_availability: {}, coverage: {} });

test('normal generation makes one atomic publish request and optionally requests the dependent Scheme', async () => {
  for (const followup of [false, true]) {
    const calls: string[] = [];
    const result = await generateAndSaveKnowledge('/projects/2', 2, { model: 'chosen', effort: 'medium' },
      new AbortController().signal, followup, async (path, options) => {
        calls.push(path); assert.equal(options.method, 'POST');
        const body = JSON.parse(String(options.body));
        assert.equal(body.publish, true); assert.equal(body.confirmed, true);
        assert.equal(body.model, 'chosen'); assert.equal(body.effort, 'medium');
        assert.equal(body.generate_scheme, followup ? true : undefined);
        return published();
      });
    assert.deepEqual(calls, ['/projects/2/codex/knowledge/brief']);
    assert.equal(result.knowledge.publication_id, 'saved-new');
    assert.equal(result.schemeRequested, true);
  }
});

test('published result requires committed project-owned summary pairing; preview parser stays strict', () => {
  const raw = published();
  assert.equal(parsePublishedKnowledgeResult(raw, 2).knowledge.summary?.generated_at, raw.brief.generated_at);
  const reordered = { ...raw, brief: { coverage: raw.brief.coverage, sources: raw.brief.sources,
    sections: raw.brief.sections, project_state: raw.brief.project_state, generated_at: raw.brief.generated_at } };
  assert.equal(parsePublishedKnowledgeResult(reordered, 2).knowledge.publication_id, 'saved-new');
  assert.throws(() => parseKnowledgePreview(raw, 2));
  assert.throws(() => parsePublishedKnowledgeResult({ ...raw, published: false }, 2), /confirm saving/);
  assert.throws(() => parsePublishedKnowledgeResult(published('foreign', 3), 2), /project/);
  assert.throws(() => parsePublishedKnowledgeResult({ ...raw, publication_id: 'other' }, 2), /publication/);
  assert.throws(() => parsePublishedKnowledgeResult({ ...raw, scheme_requested: 'yes' }, 2), /follow-up/);
  const mismatched = structuredClone(raw); mismatched.knowledge.summary.sections.overview = [{ text: 'different' } as never];
  assert.throws(() => parsePublishedKnowledgeResult(mismatched, 2), /Summary|publication/);
  const differentText = structuredClone(raw);
  differentText.brief = { ...differentText.brief, sections: { ...differentText.brief.sections,
    overview: [{ text: 'A different summary', classification: 'reported', source_ids: [] }] as never } };
  assert.throws(() => parsePublishedKnowledgeResult(differentText, 2), /does not match its saved result/);
});

test('old saved Knowledge is not evidence a failed or preview-only generation published a replacement', () => {
  const old = parseKnowledgeResponse(response(published('old', 2, '2026-11-16T00:00:00Z')), 2);
  for (const state of ['failed', 'cancelled', 'idle', 'completed'] as const)
    assert.equal(savedKnowledgeMatchesRun(old, { state, started_at: started }, 'old'), false);
  const fresh = parseKnowledgeResponse(response(), 2);
  assert.equal(savedKnowledgeMatchesRun(fresh, { state: 'completed', started_at: started }, 'old'), true);
  assert.equal(savedKnowledgeMatchesRun(fresh, { state: 'completed', started_at: started }, 'saved-new'), false);
});

test('lost publish response waits for its new run then reloads committed Knowledge without resubmitting', async () => {
  const events: any[] = [], calls: string[] = [];
  const states = [{ state: 'idle' }, { state: 'running', started_at: started }, { state: 'completed', started_at: started }];
  let reads = 0;
  await recoverPublishedKnowledge('/projects/2', 2, new AbortController().signal, event => events.push(event),
    async path => {
      calls.push(path);
      if (path.endsWith('/knowledge')) return response(++reads === 1 ? published('old', 2, '2026-11-16T00:00:00Z') : published());
      return states.shift();
    }, async () => {}, { baselinePublicationId: 'old' });
  assert.equal(reads, 2);
  assert.equal(events.at(-1).savedForRun, true);
  assert.equal(events.at(-1).publicationId, 'saved-new');
  assert.ok(calls.every(path => path.includes('/knowledge') || path.includes('/generation/status')));
  assert.ok(calls.every(path => !path.includes('/preview')));
});

test('failed and cancelled run recovery preserves old Knowledge but never signals success', async () => {
  for (const terminal of ['failed', 'cancelled', 'idle']) {
    const events: any[] = []; let reads = 0;
    const states = [{ state: 'running', started_at: started }, { state: terminal, started_at: started }];
    await recoverPublishedKnowledge('/projects/2', 2, new AbortController().signal, event => events.push(event),
      async path => path.endsWith('/knowledge') ? (++reads, response(published('old'))) : states.shift(), async () => {},
      { baselinePublicationId: 'old' });
    assert.equal(reads, 1); assert.equal(events.at(-1).savedForRun, false);
    assert.equal(events.at(-1).progress.state, terminal);
    assert.equal(events[0].result.snapshot.publication_id, 'old');
  }
});

test('final saved read failure cannot emit success or leave progress running', async () => {
  const events: any[] = []; let reads = 0;
  const states = [{ state: 'running', started_at: started }, { state: 'completed', started_at: started }];
  await assert.rejects(() => recoverPublishedKnowledge('/projects/2', 2, new AbortController().signal,
    event => events.push(event), async path => {
      if (!path.endsWith('/knowledge')) return states.shift();
      if (++reads === 1) return response(published('old'));
      throw new Error('Saved read unavailable');
    }, async () => {}, { baselinePublicationId: 'old' }), /Saved read unavailable/);
  assert.equal(events.at(-1).progress.state, 'completed');
  assert.equal(events.at(-1).savedForRun, false);
});

test('aborted or foreign generation cannot start requests or mutate saved results', async () => {
  const controller = new AbortController(); controller.abort(); let calls = 0;
  const request = async () => { calls++; return published(); };
  await assert.rejects(generateAndSaveKnowledge('/projects/2', 2, { model: '', effort: '' }, controller.signal, false, request));
  await assert.rejects(generateAndSaveKnowledge('/projects/3', 2, { model: '', effort: '' }, new AbortController().signal, false, request));
  assert.equal(calls, 0);
});
