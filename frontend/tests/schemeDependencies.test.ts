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
  saved_dependency_repository_matches: true,
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
  assert.match(legacy, /<td><small>Declaration<a [^>]*href="#1-dependency-source-1"/);
  assert.doesNotMatch(legacy, /No purpose explanation available/);
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
      coverage, repository_identity: 'first-checkout',
      dependencies: [{ ...dependency, purpose: 'Provides process isolation for the local runner.',
        purpose_source_ids: ['manifest'] }], sources: [{ ...source, text: 'Saved manifest declaration' }],
    } },
    current: { dependencies: [], dependency_inventory: {
      coverage, repository_identity: 'first-checkout',
      dependencies: [dependency], sources: [{ ...source, text: 'Current manifest declaration' }],
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
  function render(constraint: string, matches = true) {
    return renderToStaticMarkup(createElement(dependencies, {
      snapshot, current: { dependencies: [], saved_dependency_repository_matches: matches, dependency_inventory: {
        ...dependency_inventory, dependencies: [{ ...declaration, constraint }],
      } },
      historical: false, prefix: '1-dependency',
    }));
  }
  const unchanged = render('==1');
  assert.match(unchanged, /Saved purpose · .*Used for old project work/);
  assert.match(unchanged, /A supported explanation\./);
  const changed = render('==2');
  assert.match(changed, /<td><small>Declaration<a /);
  assert.doesNotMatch(changed, /Used for old project work|A supported explanation/);
  const unverified = render('==1', false);
  assert.match(unverified, /<td><small>Declaration<a /);
  assert.doesNotMatch(unverified, /Used for old project work|A supported explanation/);
});

test('relinking does not transfer a purpose even when the dependency ID and declaration match', () => {
  const declaration = { ...saved.dependencies[0], purpose: '', source_ids: ['old-source'] };
  const inventory = { ...current.dependency_inventory, dependencies: [declaration], sources: [saved.sources[0]] };
  const snapshot = { ...saved, dependency_inventory: { ...inventory, repository_identity: 'original-checkout',
    dependencies: [{ ...declaration, purpose: 'Used in the former checkout.', purpose_source_ids: ['purpose-source'] }] } };
  const active = { dependencies: [], saved_dependency_repository_matches: true,
    dependency_inventory: { ...inventory, repository_identity: 'replacement-checkout' } };
  const settings = { snapshot, current: active, prefix: '1-dependency' };
  const currentHtml = renderToStaticMarkup(createElement(dependencies, { ...settings, historical: false }));
  assert.match(currentHtml, /<td><small>Declaration<a /);
  assert.doesNotMatch(currentHtml, /Used in the former checkout|A supported explanation/);
  const historical = renderToStaticMarkup(createElement(dependencies, { ...settings, historical: true }));
  assert.match(historical, /Used in the former checkout/);
  assert.match(historical, /A supported explanation/);
});

test('package roles preserve declaration evidence and defer to project purposes within the same repository', () => {
  const role = { ...saved.dependencies[0], purpose: 'Isolates local processes.', purpose_kind: 'package_role' as const };
  const projectPurpose = { ...saved.dependencies[0], purpose: 'Runs the project worker in a sandbox.',
    purpose_kind: 'project_use' as const, purpose_source_ids: ['purpose-source'] };
  const inventory = { ...current.dependency_inventory, repository_identity: 'checkout', sources: [saved.sources[0]] };
  function render(scanned: typeof role | typeof projectPurpose, archived: typeof role | typeof projectPurpose,
    repository_identity = 'checkout') {
    return renderToStaticMarkup(createElement(dependencies, {
      snapshot: { ...saved, dependency_inventory: { ...inventory, dependencies: [archived] } },
      current: { dependencies: [], dependency_inventory: { ...inventory, repository_identity, dependencies: [scanned] } },
      historical: false, prefix: '1-dependency',
    }));
  }
  const separateCheckout = render(role, projectPurpose, 'relocated');
  assert.match(separateCheckout, /Package role · .*Isolates local processes/);
  assert.match(separateCheckout, /Declaration<a [^>]*href="#1-dependency-source-1"/);
  assert.doesNotMatch(separateCheckout, /Runs the project worker/);
  const sameCheckout = render(role, projectPurpose);
  assert.match(sameCheckout, /Saved purpose · .*Runs the project worker/);
  assert.match(sameCheckout, /A supported explanation/);
  assert.doesNotMatch(sameCheckout, /Package role/);
  const currentPurpose = render(projectPurpose, role);
  assert.match(currentPurpose, /Runs the project worker/);
  assert.doesNotMatch(currentPurpose, /Package role|Saved purpose/);
});

test('Conda dependencies show one row with exact pins and citations for every declaration', () => {
  const entry = { ...saved.dependencies[0], name: 'dask-core', category: 'environment:conda' };
  const dependenciesFromManifests = [
    { ...entry, id: 'cpu', constraint: '=2026.6.0', source_ids: ['cpu'], purpose: 'Executes parallel tasks.',
      purpose_source_ids: ['purpose-source'] },
    { ...entry, id: 'gpu', constraint: '=2026.6.0', source_ids: ['gpu'], purpose: 'Executes parallel tasks.',
      purpose_source_ids: ['purpose-source'] },
    { ...entry, id: 'old-env', constraint: '=2024.5.0=build', source_ids: ['old-env'],
      purpose: 'Schedules tasks.', purpose_kind: 'package_role' as const },
  ];
  const sources = [...['cpu', 'gpu', 'old-env'].map(id => ({
    id, label: `${id}.yml`, text: 'Declares dask-core', provenance: 'dependency manifest',
  })), saved.sources[1]];
  const html = renderToStaticMarkup(createElement(dependencies, {
    snapshot: { dependencies: dependenciesFromManifests, sources },
    historical: true, prefix: '1-dependency',
  }));
  assert.equal(html.match(/<td>dask-core<\/td>/g)?.length, 1);
  assert.match(html, /<summary>2 declared constraints<\/summary>/);
  assert.match(html, /<code>=2026\.6\.0<\/code><small>Declaration<a [^>]*href="#1-dependency-source-1"/);
  assert.match(html, /<code>=2026\.6\.0<\/code><small>Declaration<a [^>]*href="#1-dependency-source-2"/);
  assert.match(html, /<code>=2024\.5\.0=build<\/code><small>Declaration<a [^>]*href="#1-dependency-source-3"/);
  assert.equal(html.match(/Executes parallel tasks\./g)?.length, 1);
  assert.match(html, /More explanations \(1\)/);
  assert.match(html, /Package role · .*Schedules tasks/);
});

test('grouping preserves distinct ecosystems and keeps mixed runtime and development groups visible', () => {
  const entries = [
    { id: 'requirements', label: 'requirements.txt', category: 'runtime' },
    { id: 'pyproject', label: 'pyproject.toml', category: 'development:test' },
    { id: 'npm', label: 'frontend/package.json', category: 'runtime' },
    { id: 'conda', label: 'env.yml', category: 'environment:conda' },
  ];
  const html = renderToStaticMarkup(createElement(dependencies, {
    snapshot: {
      dependencies: entries.map(({ id, category }) => ({ ...saved.dependencies[0], id, category, source_ids: [id] })),
      sources: entries.map(({ id, label }) => ({ id, label, text: label, provenance: 'dependency manifest' })),
    },
    historical: true, prefix: '1-dependency',
  }));
  assert.equal(html.match(/<td>older-package<\/td>/g)?.length, 3);
  assert.match(html, /\(2 declarations\)/);
  assert.match(html, /runtime, development:test/);
  assert.doesNotMatch(html, /Development dependencies/);
});
