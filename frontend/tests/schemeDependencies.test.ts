import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';
import type { SchemeDependencies } from '../src/SchemeDependencies.tsx';

let server: ViteDevServer;
let dependencies: typeof SchemeDependencies;
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  ({ SchemeDependencies: dependencies } = await server.ssrLoadModule('/src/SchemeDependencies.tsx'));
});
after(async () => { await server?.close(); });

const saved = {
  dependencies: [{ id: 'old', name: 'older-package', constraint: '==1', category: 'runtime', purpose: '',
    source_ids: ['old-source'] }],
  sources: [
    { id: 'old-source', label: 'requirements.txt', provenance: 'dependency manifest', text: 'older-package==1' },
    { id: 'purpose-source', label: 'README.md', provenance: 'project documentation', text: 'A supported explanation.' },
  ],
};
const current = {
  dependencies: [],
  dependency_inventory: {
    dependencies: [{ id: 'import', name: 'crem', constraint: '', category: 'import:python', purpose: 'Used for project work.',
      source_ids: ['import-source'], purpose_source_ids: ['purpose-source'] }],
    sources: [{ id: 'import-source', label: 'worker.py', provenance: 'Python import', text: 'from crem import crem' }],
    coverage: { files_total: 12, files_scanned: 11, limitations: ['One file could not be parsed.'] },
  },
};

test('the current repository inventory takes precedence over saved Scheme dependencies', () => {
  const html = renderToStaticMarkup(createElement(dependencies, { snapshot: saved, current,
    historical: false, prefix: '1-dependency' }));
  assert.match(html, /11 of 12 eligible files scanned/);
  assert.ok(html.includes('<td>crem</td><td><code>—</code></td><td>Detected import</td>'));
  assert.match(html, /Dependency scan limitations/);
  assert.match(html, /One file could not be parsed/);
  assert.doesNotMatch(html, /older-package|Not specified/);
  for (const number of [1, 2]) {
    assert.ok(html.includes(`href="#1-dependency-source-${number}"`));
    assert.ok(html.includes(`id="1-dependency-source-${number}"`));
  }
  assert.ok(html.includes('worker.py'));
  assert.ok(html.includes('README.md'));
});

test('a historical Scheme uses its own inventory or its own legacy declarations', () => {
  const selection = { current, historical: true, prefix: '1-dependency' };
  const legacy = renderToStaticMarkup(createElement(dependencies, { ...selection, snapshot: saved }));
  assert.match(legacy, /older-package/);
  assert.doesNotMatch(legacy, /crem|worker.py|Saved dependency scan/);
  const historical = { ...saved, dependency_inventory: { ...current.dependency_inventory,
    dependencies: [saved.dependencies[0]], sources: [saved.sources[0]],
    coverage: { files_total: 8, files_scanned: 8, limitations: [] } } };
  const html = renderToStaticMarkup(createElement(dependencies, { ...selection, snapshot: historical }));
  assert.match(html, /Saved dependency scan · 8 of 8 eligible files scanned/);
  assert.doesNotMatch(html, /11 of 12|crem|worker.py/);
});
