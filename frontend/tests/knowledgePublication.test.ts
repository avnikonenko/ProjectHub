import assert from 'node:assert/strict';
import test from 'node:test';
import { activateKnowledgeResult } from '../src/knowledgePublication.ts';

function activated(identity = 'saved-result', projectId = 2) {
  return { published: true, publication_id: identity,
    knowledge: { project_id: projectId, publication_id: identity, items: [], sources: [] },
    brief: { generated_at: '2026-11-14T00:00:00Z', project_state: { publication_id: identity },
      sections: { overview: [], architecture: [], current_status: [] }, sources: [], coverage: [] } };
}

test('using a saved result posts only its identity and validates the resulting pair', async () => {
  const signal = new AbortController().signal;
  const raw = activated(); const original = structuredClone(raw);
  const result = await activateKnowledgeResult('/projects/2', 2, 'saved-result', signal, async (path, options) => {
    assert.equal(path, '/projects/2/codex/analysis/publish');
    assert.equal(options.method, 'POST');
    assert.equal(options.signal, signal);
    assert.deepEqual(JSON.parse(String(options.body)), { publication_id: 'saved-result' });
    return raw;
  });
  assert.equal(result.knowledge.publication_id, 'saved-result');
  assert.deepEqual(raw, original);
});

test('activation rejects foreign projects, mismatched identities and unpublished responses', async () => {
  for (const raw of [
    activated('other'), activated('saved-result', 3),
    { ...activated(), published: false },
    { ...activated(), brief: { ...activated().brief, project_state: { publication_id: 'foreign' } } },
    { ...activated(), brief: null }, null,
  ]) await assert.rejects(activateKnowledgeResult('/projects/2', 2, 'saved-result',
    new AbortController().signal, async () => raw));
  for (const [base, id] of [['/projects/3', 'saved-result'], ['/projects/2', '']]) {
    let requests = 0;
    await assert.rejects(activateKnowledgeResult(base, 2, id, new AbortController().signal, async () => {
      requests++; return activated();
    }));
    assert.equal(requests, 0);
  }
});

test('activation exposes stale-result errors without generating or retrying', async () => {
  let requests = 0;
  await assert.rejects(activateKnowledgeResult('/projects/2', 2, 'saved-result', new AbortController().signal,
    async () => { requests++; throw new Error('Saved result changed. Generate a new result.'); }), /Saved result changed/);
  assert.equal(requests, 1);
});
