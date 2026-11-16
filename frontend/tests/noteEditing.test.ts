import { test } from 'node:test';
import assert from 'node:assert/strict';
import { noteEditPatch, NOTE_CONTENT_MAX_BYTES } from '../src/noteEditing.ts';

test('metadata edits omit unchanged oversized original text and preserve records', () => {
  const original = { id: 7, title: 'Old', kind: 'document', reference: 'saved', content: 'é'.repeat(NOTE_CONTENT_MAX_BYTES / 2 + 1) };
  const values = { ...original, title: 'New' };
  assert.deepEqual(noteEditPatch(values, original), { title: 'New' });
  assert.equal(original.title, 'Old');
  assert.equal(values.content, original.content);
});

test('unchanged notes submit no content and unrelated identities are never patched', () => {
  const original = { id: 7, title: 'Saved', content: '\r\n exact source \n', kind: 'note', reference: '' };
  assert.deepEqual(noteEditPatch({ ...original, project_id: 99 }, original), {});
  assert.equal(original.content, '\r\n exact source \n');
});

test('changed content preserves exact whitespace and can shorten an oversized legacy record', () => {
  const original = { id: 7, content: 'old original' };
  assert.deepEqual(noteEditPatch({ content: '\n  smaller edited text  \n' }, original), { content: '\n  smaller edited text  \n' });
});

test('oversized new content reports its UTF-8 bound before sending a request', () => {
  assert.throws(() => noteEditPatch({ content: 'é'.repeat(NOTE_CONTENT_MAX_BYTES / 2 + 1) }, { id: 7, content: 'old' }), /Keep the original content unchanged/);
});
