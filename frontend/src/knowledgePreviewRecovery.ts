import { api } from './api.ts';
import type { SchemeFocus } from './generationBatch.ts';
import { parseKnowledgePreview, parseKnowledgeResponse, parsePublishedKnowledgeResult, type KnowledgeResponse } from './knowledge.ts';

export type KnowledgePreview = ReturnType<typeof parseKnowledgePreview>;
export type PreviewProgress = {
  state: 'idle' | 'running' | 'completed' | 'failed' | 'cancelled';
  started_at?: string;
  error?: string | null;
};

export async function generateAndSaveKnowledge(base: string, projectId: number,
  generation: { model: string; effort: string; schemeFocus?: SchemeFocus }, signal: AbortSignal, generateScheme = false,
  request: (path: string, options: RequestInit) => Promise<unknown> = api) {
  if (base !== `/projects/${projectId}` || !Number.isSafeInteger(projectId) || projectId < 1)
    throw new Error('Knowledge generation does not identify this project.');
  signal.throwIfAborted();
  const raw = await request(`${base}/codex/knowledge/brief`, { method: 'POST', signal,
    body: JSON.stringify({ confirmed: true, publish: true, model: generation.model || null,
      effort: generation.effort || null, ...(generateScheme ? { generate_scheme: true, scheme_focus: generation.schemeFocus } : {}) }) });
  signal.throwIfAborted();
  return parsePublishedKnowledgeResult(raw, projectId);
}
type Request = (path: string, options: { signal: AbortSignal }) => Promise<unknown>;
type RecoveryEvent = { type: 'saved'; result: KnowledgePreview | null }
  | { type: 'progress'; progress: PreviewProgress };

function progressResponse(raw: unknown): PreviewProgress {
  if (!raw || typeof raw !== 'object' || !('state' in raw)
      || !['idle', 'running', 'completed', 'failed', 'cancelled'].includes(String(raw.state)))
    throw new Error('Invalid knowledge generation status.');
  const status = raw as PreviewProgress;
  if ((status.started_at !== undefined && typeof status.started_at !== 'string')
      || (status.error !== undefined && status.error !== null && typeof status.error !== 'string'))
    throw new Error('Invalid knowledge generation status.');
  return status;
}

function pause(signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    const done = () => { clearTimeout(timer); signal.removeEventListener('abort', done); resolve(); };
    const timer = setTimeout(done, 1500);
    signal.addEventListener('abort', done, { once: true });
    if (signal.aborted) done();
  });
}

/** Read-only recovery. The request key and AbortSignal belong to the project view. */
export async function recoverKnowledgePreview(
  base: string, projectId: number, signal: AbortSignal,
  notify: (event: RecoveryEvent) => void, request: Request = api,
  wait: (signal: AbortSignal) => Promise<void> = pause,
  handoff?: { previousStartedAt?: string },
) {
  const saved = async () => {
    const raw = await request(`${base}/codex/analysis/preview`, { signal });
    if (!signal.aborted) notify({ type: 'saved', result: raw === null ? null : parseKnowledgePreview(raw, projectId) });
  };
  const status = async () => progressResponse(await request(`${base}/codex/generation/status?kind=knowledge`, { signal }));
  let progress: PreviewProgress;
  // Loading saved output never starts generation or publishes knowledge.
  [, progress] = await Promise.all([saved(), status()]);
  if (signal.aborted) return;
  // Only a lost local POST needs this grace. Ordinary browsing remains a
  // single read when idle, while a delayed server start cannot be missed.
  for (let attempt = 0; handoff && attempt < 2 && progress.state !== 'running'
       && progress.started_at === handoff.previousStartedAt; attempt++) {
    await wait(signal);
    if (signal.aborted) return;
    progress = await status();
    if (signal.aborted) return;
  }
  // The first saved read may race the transaction that finishes generation.
  // A read after observing completion must see its committed preview.
  try {
    if (progress.state === 'completed') await saved();
  } finally {
    if (!signal.aborted) notify({ type: 'progress', progress });
  }
  if (signal.aborted) return;
  let recovering = false;
  while (progress.state === 'running' && !signal.aborted) {
    recovering = true;
    await wait(signal);
    if (signal.aborted) return;
    progress = await status();
    if (signal.aborted) return;
    if (progress.state !== 'running') break;
    notify({ type: 'progress', progress });
  }
  if (recovering) {
    try {
      if (progress.state === 'completed') await saved();
    } finally {
      // A failed final read must not leave the UI claiming generation is active.
      if (!signal.aborted) notify({ type: 'progress', progress });
    }
  }
}

export function knowledgePreviewExport(result: KnowledgePreview, projectId: number) {
  if (result.knowledge.project_id !== projectId) throw new Error('Preview belongs to another project.');
  const publication = result.knowledge.publication_id;
  const data = { published: false, publication_id: publication,
    knowledge: result.knowledge, brief: result.brief,
    ...(result.diagnostics === undefined ? {} : { diagnostics: result.diagnostics }) };
  // Export has the same identity checks as generation and recovery.
  parseKnowledgePreview(data, projectId);
  return { filename: `project-${projectId}-knowledge-preview.json`, text: JSON.stringify(data, null, 2) + '\n' };
}

export type PublishedKnowledgeRecoveryEvent = { type: 'saved'; result: KnowledgeResponse }
  | { type: 'progress'; progress: PreviewProgress; savedForRun: boolean; publicationId?: string };

/** Display the saved pair, but never mistake an older pair for a completed new run. */
export function savedKnowledgeMatchesRun(result: KnowledgeResponse, status: PreviewProgress,
  baselinePublicationId?: string | null) {
  const snapshot = result.snapshot;
  const generated = Date.parse(snapshot?.generated_at || '');
  const started = Date.parse(status.started_at || '');
  return status.state === 'completed' && !!snapshot?.publication_id && !!snapshot.summary && !snapshot.summary_error
    && Number.isFinite(generated) && Number.isFinite(started) && generated >= started
    && (baselinePublicationId === undefined || snapshot.publication_id !== baselinePublicationId);
}

/** Read-only recovery for the normal generate-and-save workflow. */
export async function recoverPublishedKnowledge(base: string, projectId: number, signal: AbortSignal,
  notify: (event: PublishedKnowledgeRecoveryEvent) => void, request: Request = api,
  wait: (signal: AbortSignal) => Promise<void> = pause,
  handoff?: { previousStartedAt?: string; baselinePublicationId?: string | null }) {
  if (base !== `/projects/${projectId}` || !Number.isSafeInteger(projectId) || projectId < 1)
    throw new Error('Knowledge recovery does not identify this project.');
  signal.throwIfAborted();
  let result: KnowledgeResponse;
  const saved = async () => {
    result = parseKnowledgeResponse(await request(`${base}/codex/knowledge`, { signal }), projectId);
    if (!signal.aborted) notify({ type: 'saved', result });
  };
  const readStatus = async () => progressResponse(await request(`${base}/codex/generation/status?kind=knowledge`, { signal }));
  let status: PreviewProgress;
  [, status] = await Promise.all([saved(), readStatus()]);
  if (signal.aborted) return;
  for (let attempt = 0; handoff && attempt < 2 && status.state !== 'running'
       && status.started_at === handoff.previousStartedAt; attempt++) {
    await wait(signal); if (signal.aborted) return;
    status = await readStatus(); if (signal.aborted) return;
  }
  const progressEvent = (confirmed = true) => {
    if (!signal.aborted) notify({ type: 'progress', progress: status,
      savedForRun: confirmed && savedKnowledgeMatchesRun(result!, status, handoff?.baselinePublicationId),
      publicationId: result!.snapshot?.publication_id });
  };
  if (status.state === 'completed') {
    let confirmed = false;
    try { await saved(); confirmed = true; } finally { progressEvent(confirmed); }
  } else progressEvent();
  while (status.state === 'running' && !signal.aborted) {
    await wait(signal); if (signal.aborted) return;
    status = await readStatus(); if (signal.aborted) return;
    if (status.state === 'completed') {
      let confirmed = false;
      try { await saved(); confirmed = true; } finally { progressEvent(confirmed); }
    } else progressEvent();
  }
}
