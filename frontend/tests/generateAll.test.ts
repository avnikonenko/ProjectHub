import { test } from 'node:test';
import assert from 'node:assert/strict';
import { claimGenerationRequest, idleGenerationKinds, projectGenerationKinds,
  planProjectGeneration, resolveKnowledgeSchemeDependency,
  type GenerationRequestTracker } from '../src/generationBatch.ts';

test('Regenerate all includes each independent view and limits, without tags', () => {
  assert.deepEqual(idleGenerationKinds({}), ['brief', 'knowledge', 'scheme', 'qa', 'parameters']);
  assert.equal(projectGenerationKinds.length, 5);
  assert.deepEqual(idleGenerationKinds({ brief: true, knowledge: true, qa: true }), ['scheme', 'parameters']);
});

test('Regenerate all starts independent views but defers Scheme until Knowledge is saved', () => {
  assert.deepEqual(planProjectGeneration({}), {
    start: ['brief', 'knowledge', 'qa', 'parameters'], waitForKnowledge: true, schemeSkippedForKnowledge: false,
  });
  assert.deepEqual(planProjectGeneration({ knowledge: true }), {
    start: ['brief', 'qa', 'parameters'], waitForKnowledge: false, schemeSkippedForKnowledge: true,
  });
  assert.deepEqual(planProjectGeneration({ scheme: true }), {
    start: ['brief', 'knowledge', 'qa', 'parameters'], waitForKnowledge: false, schemeSkippedForKnowledge: false,
  });
});

test('Scheme recovery follows only matching, saved Knowledge with an accepted server follow-up', () => {
  const pending = { project: '/projects/1', request: 2 };
  const saved = { request: 2, state: 'saved' as const, publicationId: 'new-knowledge', schemeRequested: true };
  assert.equal(resolveKnowledgeSchemeDependency(pending, '/projects/1', saved), 'start');
  assert.equal(resolveKnowledgeSchemeDependency(pending, '/projects/2', saved), 'ignore');
  assert.equal(resolveKnowledgeSchemeDependency(pending, '/projects/1', { ...saved, request: 1 }), 'ignore');
  assert.equal(resolveKnowledgeSchemeDependency(null, '/projects/1', saved), 'ignore');
  assert.equal(resolveKnowledgeSchemeDependency(pending, '/projects/1', { ...saved, publicationId: '' }), 'skip');
  assert.equal(resolveKnowledgeSchemeDependency(pending, '/projects/1', { ...saved, schemeRequested: false }), 'skip');
  for (const state of ['failed', 'cancelled'] as const)
    assert.equal(resolveKnowledgeSchemeDependency(pending, '/projects/1', { request: 2, state }), 'skip');
});

test('bulk dispatch waits for initial reads, claims once before generation and skips a busy view', () => {
  const tracker: GenerationRequestTracker = { identity: '/projects/1', sequence: 0 };
  assert.equal(claimGenerationRequest(tracker, '/projects/1', 1, 'wait'), null);
  assert.equal(tracker.sequence, 0);
  assert.equal(claimGenerationRequest(tracker, '/projects/1', 1, 'ready'), 'start');
  assert.equal(claimGenerationRequest(tracker, '/projects/1', 1, 'ready'), null);
  assert.equal(claimGenerationRequest(tracker, '/projects/1', 2, 'skip'), 'skip');
  assert.equal(claimGenerationRequest(tracker, '/projects/1', 2, 'ready'), null);
});

test('view requests remain independent and project changes do not reuse consumed request state', () => {
  const trackers = projectGenerationKinds.map(() => ({ identity: '/projects/1', sequence: 0 }));
  assert.deepEqual(trackers.map(tracker => claimGenerationRequest(tracker, '/projects/1', 1, 'ready')),
    ['start', 'start', 'start', 'start', 'start']);
  assert.equal(claimGenerationRequest(trackers[0], '/projects/2', 1, 'ready'), 'start');
  assert.equal(claimGenerationRequest(trackers[0], '/projects/2', 0, 'ready'), null);
  assert.equal(claimGenerationRequest(trackers[0], '/projects/2', NaN, 'ready'), null);
});
