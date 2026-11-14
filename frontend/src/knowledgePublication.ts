import { api } from './api.ts';
import { parseKnowledgePreview } from './knowledge.ts';

type Request = (path: string, options: RequestInit) => Promise<unknown>;

/** Activate the saved result by identity; client interpretations are never uploaded. */
export async function activateKnowledgeResult(base: string, projectId: number, publicationId: string,
  signal: AbortSignal, request: Request = api) {
  if (base !== `/projects/${projectId}` || !publicationId.trim())
    throw new Error('The saved result does not identify this project.');
  const raw = await request(`${base}/codex/knowledge/preview/publish`, {
    method: 'POST', signal, body: JSON.stringify({ publication_id: publicationId }),
  });
  if (!raw || typeof raw !== 'object' || !('published' in raw) || raw.published !== true
    || !('publication_id' in raw) || raw.publication_id !== publicationId)
    throw new Error('The activated result does not match the selected saved result.');
  // The pair uses the same validated shape as a candidate; only its activation
  // flag differs. Preserve project/knowledge/brief identity checks in one parser.
  return parseKnowledgePreview({ ...raw, published: false }, projectId);
}
