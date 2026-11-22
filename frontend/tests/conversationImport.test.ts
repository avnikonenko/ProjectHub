import assert from 'node:assert/strict';
import test from 'node:test';
import { conversationImportBody, conversationReference, conversationSourcesPath,
  hasConversationMetadata, importedConversation, linkedClaudeSession, parseCodexSessionPreview, parseConversationPreview, parseConversationSources,
  readConversationFile } from '../src/conversationImportData.ts';

const chatgpt = 'https://chatgpt.com/share/00000000-0000-4000-8000-000000000001';
const source = { filename: 'Original.JSON', content: '\uFEFF{"messages":[]}\r\n', source_url: chatgpt };
const fixture = () => parseConversationPreview({
  title: 'One conversation', provider: 'chatgpt', completeness: 'unknown',
  source_status: 'provided', reference: chatgpt, message_count: 2,
  role_counts: { user: 1, assistant: 1 }, sample: [{ role: 'user', text: '<script>plain text</script>' }],
  fingerprint: 'checked-exact-input', existing: null,
});

test('import body binds the exact previewed file or link to its fingerprint', () => {
  const preview = fixture();
  assert.equal(preview.sample[0].text, '<script>plain text</script>');
  assert.deepEqual(conversationImportBody(source, preview, false, false),
    { ...source, fingerprint: 'checked-exact-input' });
  assert.deepEqual(conversationImportBody({ url: chatgpt }, preview, true, true),
    { url: chatgpt, fingerprint: 'checked-exact-input' });
});

test('duplicates cannot be saved and partial replacement needs both explicit confirmations', () => {
  const preview = { ...fixture(), completeness: 'partial' as const,
    existing: { id: 3, title: 'Saved conversation', duplicate: false } };
  assert.equal(conversationImportBody(source, preview, false, true), null);
  assert.equal(conversationImportBody(source, preview, true, false), null);
  assert.deepEqual(conversationImportBody(source, preview, true, true), {
    ...source, fingerprint: 'checked-exact-input', confirm_partial: true, replace_existing: true,
  });
  assert.equal(conversationImportBody(source, { ...preview, existing: { ...preview.existing, duplicate: true } },
    true, true), null);
  assert.throws(() => parseConversationPreview({ ...preview, role_counts: { user: 0, assistant: 1 } }), /invalid data/);
});

test('uploaded UTF-8 bytes are retained including BOM and line endings', async () => {
  const controller = new AbortController();
  const bytes = new TextEncoder().encode(source.content);
  const file = new File([bytes], source.filename);
  assert.equal(await readConversationFile(file, controller.signal), source.content);
  assert.deepEqual(new TextEncoder().encode(await readConversationFile(file, controller.signal)), bytes);
  await assert.rejects(readConversationFile(new File([], 'empty.md'), controller.signal), /conversation file is empty/);
  await assert.rejects(readConversationFile(new File([new Uint8Array([0xC0])], 'invalid.md'), controller.signal), /valid UTF-8/);
  let read = false;
  const oversized = { name: 'large.json', size: 16 * 1024 * 1024 + 1, arrayBuffer() { read = true; } } as unknown as File;
  await assert.rejects(readConversationFile(oversized, controller.signal), /16 MiB/);
  assert.equal(read, false);
  const pending = { name: 'cancelled.md', size: bytes.length, async arrayBuffer() {
    controller.abort();
    return bytes.buffer;
  } } as File;
  await assert.rejects(readConversationFile(pending, controller.signal), { name: 'AbortError' });
});

test('history recognises canonical local imports and legacy shared links while keeping original downloads scoped', () => {
  const local = { id: 3, kind: 'document', project_id: 2, conversation: {
    provider: 'claude', completeness: 'partial', source_status: 'none',
  } };
  assert.equal(importedConversation(local)?.provider, 'claude');
  assert.equal(hasConversationMetadata(local), true);
  const legacy = { id: 4, kind: 'document', reference: chatgpt };
  assert.equal(importedConversation(legacy)?.provider, 'chatgpt');
  assert.equal(hasConversationMetadata(legacy), false);
  assert.equal(importedConversation({ id: 5, kind: 'document', reference: 'https://example.org' }), null);
  assert.equal(conversationReference(chatgpt), chatgpt);
  assert.equal(conversationReference('javascript:alert(1)'), null);
  assert.equal(conversationReference('https://user:secret@example.org/'), null);
  assert.equal(conversationSourcesPath('/projects/2', local), '/projects/2/conversations/3/sources');
  assert.throws(() => conversationSourcesPath('/projects/1', local), /selected project/);
  assert.throws(() => conversationSourcesPath('/projects/2/../1', local), /selected project/);
  const copy = { id: 1, filename: 'Original.JSON', created_at: '2026-11-20T10:00:00Z' };
  assert.deepEqual(parseConversationSources([copy]), [copy]);
  assert.throws(() => parseConversationSources([copy, copy]), /could not be read/);
});

test('linked Claude refresh retains the saved session identity and confirms only its preview', () => {
  const id = '00000000-0000-4000-8000-000000000002';
  const note = { id: 3, kind: 'document', title: 'Renamed session', conversation: {
    provider: 'claude', completeness: 'unknown', source_status: 'provided',
    linked_session: { provider: 'claude', id, profile: 'registered:7' },
  } };
  const initial = linkedClaudeSession(note);
  assert.deepEqual(initial, { session_id: id, profile: 'registered:7', title: 'Renamed session' });
  assert.equal(linkedClaudeSession({ ...note, conversation: {
    ...note.conversation, linked_session: { ...note.conversation.linked_session, profile: '../another' },
  } }), null);
  const update = { ...fixture(), existing: { id: 3, title: note.title, duplicate: false } };
  assert.equal(conversationImportBody(initial!, update, false, false), null);
  assert.deepEqual(conversationImportBody(initial!, update, false, true),
    { ...initial, fingerprint: 'checked-exact-input', replace_existing: true });
});

test('Codex session previews accept labeled text and reject malformed responses', () => {
  assert.deepEqual(parseCodexSessionPreview({ title: 'Example', sample: [] }), { title: 'Example', sample: [] });
  assert.throws(() => parseCodexSessionPreview({ title: 'Example', sample: [{ role: 'tool', text: 'hidden' }] }), /invalid data/);
  assert.throws(() => parseCodexSessionPreview({ title: 'Example' }), /invalid data/);
});
