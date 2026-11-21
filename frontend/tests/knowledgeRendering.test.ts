import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';
import { visibleRelatedKnowledge, type KnowledgeItem, type KnowledgeSnapshot } from '../src/knowledge.ts';
import { nextDisclosureState } from '../src/disclosureState.ts';

let server: ViteDevServer;
let KnowledgeItems: any;
before(async () => {
  server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  KnowledgeItems = (await server.ssrLoadModule('/src/KnowledgeItems.tsx')).KnowledgeItems;
});
after(async () => { await server?.close(); });
const makeSnapshot = (): KnowledgeSnapshot => ({
  project_id: 2, publication_id: 'render-audit', sources: [{ id: 'shared', label: 'Original source' }],
  items: Array.from({ length: 1000 }, (_, i): KnowledgeItem => ({
    id: `item-${String(i).padStart(4, '0')}`, statement: `Distinct choice ${i}`, category: 'REQUIREMENT',
    design_status: 'AGREED', scope: 'Project', agreement_basis: 'direct_user', source_state: 'valid',
    rationale: 'Saved rationale stays available on expansion.', evidence: [{ source_id: 'shared',
      role: 'user', excerpt: 'Exact source quotation stays available on expansion.', revision: 'a'.repeat(64) }],
  })),
});

test('a collapsed thousand-record passage renders one card and no hidden evidence bodies', () => {
  const snapshot = makeSnapshot();
  const before = structuredClone(snapshot);
  const html = renderToStaticMarkup(createElement(KnowledgeItems, { snapshot }));
  assert.equal((html.match(/class="knowledge-item"/g) || []).length, 1);
  assert.ok(html.includes('1000 knowledge items'));
  assert.ok(html.includes('1000 records'));
  assert.ok(!html.includes('Exact source quotation'));
  assert.ok(!html.includes('Saved rationale'));
  assert.ok(html.length < 15_000, String(html.length));
  assert.deepEqual(snapshot, before);
});

test('a selected related record beyond the first page mounts immediately while unrelated records stay bounded', () => {
  const snapshot = makeSnapshot();
  const html = renderToStaticMarkup(createElement(KnowledgeItems, { snapshot, selectedItemId: 'item-0999' }));
  assert.equal((html.match(/class="knowledge-item"/g) || []).length, 12);
  assert.ok(html.includes('Distinct choice 999'));
  assert.ok(html.includes('Show more related records'));
  assert.ok(html.includes('Saved rationale'));
  assert.ok(!html.includes('Exact source quotation'));
});

test('a historical result shows saved evidence status separately from the active view', () => {
  const snapshot = makeSnapshot();
  snapshot.items = snapshot.items.slice(0, 1);
  const selectedItemId = snapshot.items[0].id;
  const historical = renderToStaticMarkup(createElement(KnowledgeItems, { snapshot, selectedItemId, historical: true }));
  const active = renderToStaticMarkup(createElement(KnowledgeItems, {
    snapshot, selectedItemId, availability: { [selectedItemId]: 'unavailable' },
  }));
  assert.match(historical, /<dt>Evidence at time of generation<\/dt><dd>valid<\/dd>/);
  assert.doesNotMatch(historical, /Current evidence/);
  assert.match(active, /<dt>Current evidence<\/dt><dd>unavailable/);
});

test('related pagination preserves each distinct record and selected identity without merging', () => {
  const items = makeSnapshot().items;
  assert.deepEqual(visibleRelatedKnowledge(items, 10).map(item => item.id), items.slice(0, 10).map(item => item.id));
  const selected = visibleRelatedKnowledge(items, 10, 'item-0999');
  assert.equal(selected.length, 11);
  assert.equal(selected.at(-1), items[999]);
  assert.equal(visibleRelatedKnowledge(items, 1000).length, 1000);
  assert.equal(visibleRelatedKnowledge(items, 1000, 'item-0999').length, 1000);
});

test('returning from Overview to the same manually closed selection reopens it', () => {
  const closed = { key: 'publication:item-a', open: false };
  assert.equal(nextDisclosureState(closed, closed.key), closed, 'manual close is respected while selection is unchanged');
  const deselected = nextDisclosureState(closed, undefined);
  assert.equal(deselected.key, undefined);
  assert.equal(deselected.open, false);
  const revisited = nextDisclosureState(deselected, 'publication:item-a');
  assert.equal(revisited.open, true);
  const open = { key: 'publication:item-a', open: true };
  assert.equal(nextDisclosureState(open, undefined).open, true, 'clearing selection does not close a manually opened disclosure');
});
