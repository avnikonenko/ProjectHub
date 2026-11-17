import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, type ViteDevServer } from 'vite';

let server: ViteDevServer;
let repositoryOverview: any;
before(async () => {
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  repositoryOverview = (await server.ssrLoadModule('/src/RepositoryOverview.tsx')).RepositoryOverview;
});
after(async () => { await server?.close(); });

const local = { hash: 'abc1234', committed_at: '2026-11-16T10:00:00Z', subject: 'Local work' };
const remote = { hash: 'def5678', committed_at: '2026-11-15T16:00:00Z' };
const upstream = { branch: 'origin/main', is_remote: true, ahead: 2, behind: 1, commit: remote };
const render = (status: Record<string, unknown>) => renderToStaticMarkup(createElement(repositoryOverview, {
  path: '/example', loading: false, error: '', reload: () => {},
  status: { branch: 'main', dirty: true, tracked_changes: 0, untracked_files: 1, commits: [local], ...status },
}));

test('repository shows tracked files and both saved commit times for a divergent branch', () => {
  const html = render({ tracked_files: 9, upstream });
  assert.match(html, /<dt>Files<\/dt><dd>9 tracked · 1 untracked/);
  assert.match(html, /Branch sync <span class="muted">· 2 ahead · 1 behind/);
  assert.match(html, /<code>origin\/main<\/code>/);
  assert.match(html, /Local commit<\/dt><dd><code>abc1234<\/code> · <time dateTime="2026-11-16T10:00:00Z"/);
  assert.match(html, /Remote commit<\/dt><dd><code>def5678<\/code> · <time dateTime="2026-11-15T16:00:00Z"/);
});

test('an unavailable comparison is distinct from a matching remote', () => {
  assert.match(render({ upstream: { ...upstream, ahead: 0, behind: 0 } }), /Matches saved remote/);
  const shallow = render({ upstream: { ...upstream, ahead: null, behind: null } });
  assert.match(shallow, /Latest commits differ/);
  assert.match(shallow, /Ahead and behind counts are unavailable/);
  assert.doesNotMatch(shallow, /Matches saved remote/);
  assert.match(render({ upstream: { ...upstream, commit: null } }), /Upstream commit unavailable/);
  assert.match(render({ upstream: null }), /No upstream configured/);
  assert.match(render({ upstream: null, branch: null }), /Branch sync <span class="muted">· Detached HEAD/);
  assert.doesNotMatch(render({}), /Branch sync/);
});

test('the remote address comes first, followed by the local path and branch details', () => {
  const address = 'https://github.com/example/repository';
  const html = render({ remote_repository: { name: 'origin', address, web_url: address } });
  assert.match(html, /<dt>Local<\/dt><dd><code class="repository">\/example/);
  assert.match(html, /<dt title="origin">Remote<\/dt>/);
  assert.match(html, /<dt title="origin">Remote<\/dt>[\s\S]*<dt>Local<\/dt>[\s\S]*<dt>Branch<\/dt>/);
  assert.match(html, /href="https:\/\/github\.com\/example\/repository" target="_blank" rel="noopener noreferrer"/);
  assert.doesNotMatch(render({ remote_repository: { name: 'origin', address: 'ssh://example.test/code.git' } }), /<a\b/);
});

test('matching local and saved remote commits share one row', () => {
  const html = render({ upstream: { ...upstream, ahead: 0, behind: 0, commit: local } });
  assert.match(html, /Shared commit<\/dt><dd><code>abc1234<\/code>/);
  assert.equal((html.match(/<time /g) ?? []).length, 1);
  assert.doesNotMatch(html, /Local commit|Remote commit/);
});
