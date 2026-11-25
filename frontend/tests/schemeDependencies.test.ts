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
  assert.match(legacy, /No purpose explanation available/);
  assert.doesNotMatch(legacy, /crem|worker.py|Saved dependency scan/);
  const historical = { ...saved, dependency_inventory: { ...current.dependency_inventory,
    dependencies: [saved.dependencies[0]], sources: [saved.sources[0]],
    coverage: { files_total: 8, files_scanned: 8, limitations: [] } } };
  const html = renderToStaticMarkup(createElement(dependencies, { ...selection, snapshot: historical }));
  assert.match(html, /Saved dependency scan · 8 of 8 eligible files scanned/);
  assert.doesNotMatch(html, /11 of 12|crem|worker.py/);
});

test('an unchanged Conda declaration keeps its saved purpose and exact saved citation', () => {
  const dependency = { id: 'bubblewrap-in-environment', name: 'bubblewrap', constraint: '', category: 'environment:conda',
    purpose: '', source_ids: ['manifest'] };
  const source = { id: 'manifest', label: 'environment.yml', provenance: 'dependency manifest' };
  const coverage = { files_total: 1, files_scanned: 1, limitations: [] };
  const html = renderToStaticMarkup(createElement(dependencies, {
    snapshot: { dependencies: [], dependency_inventory: {
      coverage, dependencies: [{ ...dependency, purpose: 'Provides process isolation for the local runner.',
        purpose_source_ids: ['manifest'] }], sources: [{ ...source, text: 'Saved manifest declaration' }],
    } },
    current: { dependencies: [], dependency_inventory: {
      coverage, dependencies: [dependency], sources: [{ ...source, text: 'Current manifest declaration' }],
    } },
    historical: false, prefix: '1-dependency',
  }));
  assert.match(html, /Saved purpose · .*Provides process isolation for the local runner/);
  assert.doesNotMatch(html, /No purpose explanation available/);
  assert.match(html, /runner\.<a [^>]*href="#1-dependency-source-2"/);
  assert.match(html, /Declaration<a [^>]*href="#1-dependency-source-1"/);
  assert.match(html, /id="1-dependency-source-1"[^>]*>.*Current manifest declaration/);
  assert.match(html, /id="1-dependency-source-2"[^>]*>.*Saved manifest declaration/);
});

test('a current declaration can use a matching legacy purpose without transferring it to another constraint', () => {
  const declaration = { ...saved.dependencies[0], purpose: '', source_ids: ['new-manifest'] };
  const dependency_inventory = { ...current.dependency_inventory,
    sources: [{ id: 'new-manifest', label: 'requirements.txt', provenance: 'dependency manifest', text: 'Current declaration' }] };
  const snapshot = { ...saved, dependencies: [{ ...saved.dependencies[0], purpose: 'Used for old project work.',
    purpose_source_ids: ['purpose-source'] }] };
  function render(constraint: string) {
    return renderToStaticMarkup(createElement(dependencies, {
      snapshot, current: { dependencies: [], dependency_inventory: {
        ...dependency_inventory, dependencies: [{ ...declaration, constraint }],
      } },
      historical: false, prefix: '1-dependency',
    }));
  }
  const unchanged = render('==1');
  assert.match(unchanged, /Saved purpose · .*Used for old project work/);
  assert.match(unchanged, /A supported explanation\./);
  const changed = render('==2');
  assert.match(changed, /No purpose explanation available/);
  assert.doesNotMatch(changed, /Used for old project work|A supported explanation/);
});
