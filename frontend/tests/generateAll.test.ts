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

test('Regenerate all leaves a persisted queued Scheme alone after reload', () => {
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


test('Retry unfinished keeps saved views and running jobs untouched', async () => {
  const { planRetryProjectGeneration } = await import('../src/generationBatch.ts');
  const views = projectGenerationKinds.map(kind => ({ kind, state: 'saved', saved_at: '2026-11-17T12:00:00Z', retryable: false }));
  views.find(view => view.kind === 'qa')!.state = 'timed_out';
  views.find(view => view.kind === 'qa')!.retryable = true;
  views.find(view => view.kind === 'parameters')!.state = 'cancelled';
  views.find(view => view.kind === 'parameters')!.retryable = true;
  assert.deepEqual(planRetryProjectGeneration(views, {}), {
    start: ['qa', 'parameters'], waitForKnowledge: false, schemeSkippedForKnowledge: false,
  });
  assert.deepEqual(planRetryProjectGeneration(views, { qa: true }), {
    start: ['parameters'], waitForKnowledge: false, schemeSkippedForKnowledge: false,
  });
});

test('Retry unfinished retries Scheme alone when Knowledge was saved, and chains only when both unfinished', async () => {
  const { planRetryProjectGeneration } = await import('../src/generationBatch.ts');
  const views = projectGenerationKinds.map(kind => ({ kind, state: 'saved', saved_at: 'saved', retryable: false }));
  Object.assign(views.find(view => view.kind === 'scheme')!, { state: 'failed', retryable: true });
  assert.deepEqual(planRetryProjectGeneration(views, {}), {
    start: ['scheme'], waitForKnowledge: false, schemeSkippedForKnowledge: false,
  });
  Object.assign(views.find(view => view.kind === 'knowledge')!, { state: 'timed_out', retryable: true });
  assert.deepEqual(planRetryProjectGeneration(views, {}), {
    start: ['knowledge'], waitForKnowledge: true, schemeSkippedForKnowledge: false,
  });
  Object.assign(views.find(view => view.kind === 'scheme')!, { state: 'saved', retryable: false });
  assert.deepEqual(planRetryProjectGeneration(views, {}), {
    start: ['knowledge'], waitForKnowledge: false, schemeSkippedForKnowledge: false,
  });
});

test('Retry unfinished regenerates Knowledge when only an earlier result exists before dependent Scheme', async () => {
  const { planRetryProjectGeneration } = await import('../src/generationBatch.ts');
  const views = projectGenerationKinds.map(kind => ({ kind, state: 'saved', saved_at: 'saved' as string | null, retryable: false }));
  Object.assign(views.find(view => view.kind === 'knowledge')!, {
    state: 'earlier_result', saved_at: null, retryable: true,
  });
  assert.deepEqual(planRetryProjectGeneration(views, {}), {
    start: ['knowledge'], waitForKnowledge: false, schemeSkippedForKnowledge: false,
  });
  Object.assign(views.find(view => view.kind === 'scheme')!, { state: 'failed', retryable: true });
  assert.deepEqual(planRetryProjectGeneration(views, {}), {
    start: ['knowledge'], waitForKnowledge: true, schemeSkippedForKnowledge: false,
  });
  assert.deepEqual(planRetryProjectGeneration(views, { knowledge: true }), {
    start: [], waitForKnowledge: false, schemeSkippedForKnowledge: true,
  });
});

test('Retry unfinished never starts Scheme while Knowledge is running or trusts an incorrectly retryable saved view', async () => {
  const { planRetryProjectGeneration } = await import('../src/generationBatch.ts');
  const views = projectGenerationKinds.map(kind => ({ kind, state: 'saved', saved_at: 'saved', retryable: true }));
  assert.deepEqual(planRetryProjectGeneration(views, {}), {
    start: [], waitForKnowledge: false, schemeSkippedForKnowledge: false,
  });
  Object.assign(views.find(view => view.kind === 'scheme')!, { state: 'skipped', retryable: true });
  Object.assign(views.find(view => view.kind === 'knowledge')!, { state: 'waiting', retryable: false });
  assert.deepEqual(planRetryProjectGeneration(views, {}), {
    start: [], waitForKnowledge: false, schemeSkippedForKnowledge: true,
  });
});


test('explicit selection refreshes only checked idle views, including previously saved results', async () => {
  const { planSelectedProjectGeneration } = await import('../src/generationBatch.ts');
  const views = projectGenerationKinds.map(kind => ({ kind, state: 'saved', saved_at: 'saved', retryable: false }));
  assert.deepEqual(planSelectedProjectGeneration([], views, {}), {
    start: [], waitForKnowledge: false, schemeSkippedForKnowledge: false,
  });
  assert.deepEqual(planSelectedProjectGeneration(['qa'], views, {}), {
    start: ['qa'], waitForKnowledge: false, schemeSkippedForKnowledge: false,
  });
  assert.deepEqual(planSelectedProjectGeneration(['qa', 'parameters'], views, { qa: true }), {
    start: ['parameters'], waitForKnowledge: false, schemeSkippedForKnowledge: false,
  });
  assert.deepEqual(planSelectedProjectGeneration(['knowledge'], views, {}), {
    start: ['knowledge'], waitForKnowledge: false, schemeSkippedForKnowledge: false,
  });
  assert.deepEqual(planSelectedProjectGeneration(['knowledge', 'scheme'], views, {}), {
    start: ['knowledge'], waitForKnowledge: true, schemeSkippedForKnowledge: false,
  });
});

test('explicit Scheme-only selection can use earlier saved Knowledge but cannot race its active replacement', async () => {
  const { planSelectedProjectGeneration } = await import('../src/generationBatch.ts');
  const views = projectGenerationKinds.map(kind => ({ kind, state: 'saved', saved_at: 'saved', retryable: false }));
  Object.assign(views.find(view => view.kind === 'knowledge')!, { state: 'failed', retryable: true });
  assert.deepEqual(planSelectedProjectGeneration(['scheme'], views, {}), {
    start: ['scheme'], waitForKnowledge: false, schemeSkippedForKnowledge: false,
  });
  Object.assign(views.find(view => view.kind === 'knowledge')!, { state: 'running' });
  assert.deepEqual(planSelectedProjectGeneration(['scheme'], views, {}), {
    start: [], waitForKnowledge: false, schemeSkippedForKnowledge: true,
  });
  Object.assign(views.find(view => view.kind === 'knowledge')!, { state: 'not_generated', saved_at: null });
  assert.deepEqual(planSelectedProjectGeneration(['scheme'], views, {}), {
    start: [], waitForKnowledge: false, schemeSkippedForKnowledge: true,
  });
});
