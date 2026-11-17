import { api } from './api.ts';
import { parseKnowledgePreview } from './knowledge.ts';

export type KnowledgePreview = ReturnType<typeof parseKnowledgePreview>;
export type PreviewProgress = {
  state: 'idle' | 'running' | 'completed' | 'failed' | 'cancelled';
  started_at?: string;
  error?: string | null;
};
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
