import type { RecordData } from './types';

export const NOTE_CONTENT_MAX_BYTES = 16 * 1024 * 1024;

/** Preserve legacy oversized originals by omitting unchanged content from PATCH. */
export function noteEditPatch(values: Record<string, unknown>, original: RecordData): Record<string, unknown> {
  const changed = Object.fromEntries(['title', 'content', 'kind', 'reference']
    .filter(key => Object.hasOwn(values, key) && values[key] !== original[key])
    .map(key => [key, values[key]]));
  if (typeof changed.content === 'string' && new Blob([changed.content]).size > NOTE_CONTENT_MAX_BYTES) {
    throw new Error('Edited content exceeds 16 MiB of UTF-8 text. Keep the original content unchanged to save other fields, or shorten the edited text.');
  }
  return changed;
}
