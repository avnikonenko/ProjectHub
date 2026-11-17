import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createEditorIdentity } from '../src/editorIdentity.ts';

test('a delayed A load cannot replace B or its unsaved values and save target', async () => {
  const editor = createEditorIdentity();
  let releaseA!: () => void;
  let form: { id: number; target: string; value: string } | undefined;
  const a = editor.begin();
  const pendingA = new Promise<void>(resolve => { releaseA = resolve; }).then(() => {
    if (editor.current(a)) form = { id: a, target: '/notes/A', value: 'Original A' };
  });
  const b = editor.begin();
  if (editor.current(b)) form = { id: b, target: '/notes/B', value: 'Original B' };
  form!.value = 'Unsaved B';
  releaseA();
  await pendingA;
  assert.deepEqual(form, { id: b, target: '/notes/B', value: 'Unsaved B' });
});

test('cancelled loads and unmounted project workspaces cannot open editors', () => {
  const editor = createEditorIdentity();
  const pending = editor.begin();
  editor.invalidate();
  assert.equal(editor.current(pending), false);
  const newProject = createEditorIdentity();
  const current = newProject.begin();
  assert.equal(newProject.current(current), true);
  assert.equal(editor.current(pending), false);
});

test('opening another form invalidates pending note completion and old save/close callbacks', () => {
  const editor = createEditorIdentity();
  const pendingNote = editor.begin();
  const projectForm = editor.begin();
  assert.equal(editor.current(pendingNote), false);
  assert.equal(editor.current(projectForm), true);
  const nextForm = editor.begin();
  // An old completion may only close a form if its captured identity is current.
  if (editor.current(projectForm)) editor.invalidate();
  assert.equal(editor.current(nextForm), true);
});

test('every form receives a distinct identity even for the same record', () => {
  const editor = createEditorIdentity();
  const first = editor.begin();
  const second = editor.begin();
  assert.notEqual(first, second);
  assert.equal(editor.current(first), false);
  assert.equal(editor.current(second), true);
});
