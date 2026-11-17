import { Feedback } from './Feedback';
import { useEffect, useState } from 'react';
import { api } from './api';
import { ErrorBox, PagedItems } from './ui';

type CommitTip = { hash: string; committed_at?: string };
type RepositoryUpstream = {
  branch: string;
  is_remote: boolean;
  ahead: number | null;
  behind: number | null;
  commit: CommitTip | null;
};
type RepositoryStatus = {
  branch: string | null;
  dirty: boolean;
  branches?: string[];
  branches_limited?: boolean;
  remote_branches?: string[];
  remote_branches_limited?: boolean;
  remote_repository?: { name: string; address: string; web_url?: string | null } | null;
  tracked_files?: number | null;
  tracked_changes?: number;
  untracked_files?: number;
  upstream?: RepositoryUpstream | null;
  upstream_unavailable?: boolean;
  github_issues_url?: string | null;
  commits: (CommitTip & { subject: string })[];
};

function commitTip(commit: CommitTip | null | undefined, unavailable: string) {
  if (!commit) return unavailable;
  const timestamp = commit.committed_at && Number.isFinite(Date.parse(commit.committed_at)) ? commit.committed_at : null;
  return <><code>{commit.hash}</code>{timestamp && <> · <time dateTime={timestamp}>{new Date(timestamp).toLocaleString()}</time></>}</>;
}

function BranchSync({ status }: { status: RepositoryStatus }) {
  const upstream = status.upstream;
  const localCommit = status.commits[0];
  const countsUnavailable = upstream?.commit && (upstream.ahead === null || upstream.behind === null);
  let summary = 'No upstream configured';
  if (status.branch === null) summary = 'Detached HEAD';
  else if (status.upstream_unavailable) summary = 'Upstream check unavailable';
  else if (upstream) {
    if (!upstream.commit) summary = 'Upstream commit unavailable';
    else if (upstream.ahead === null || upstream.behind === null) summary = !localCommit ? 'Commit comparison unavailable'
      : localCommit.hash === upstream.commit.hash ? 'Latest commits match' : 'Latest commits differ';
    else if (upstream.ahead || upstream.behind) {
      summary = `${upstream.ahead.toLocaleString()} ahead · ${upstream.behind.toLocaleString()} behind`;
    } else summary = upstream.is_remote ? 'Matches saved remote' : 'Matches upstream';
  }
  return <details className="repository-branches repository-sync">
    <summary>Branch sync · {summary}</summary>
    <dl>
      {upstream && <><dt>Upstream</dt><dd><code>{upstream.branch}</code></dd></>}
      <dt>Latest local commit</dt><dd>{commitTip(localCommit, 'No commit yet')}</dd>
      {upstream && <><dt>{upstream.is_remote ? 'Latest saved remote commit' : 'Latest upstream commit'}</dt>
        <dd>{commitTip(upstream.commit, 'Unavailable')}</dd></>}
    </dl>
    {countsUnavailable && <p className="muted"><small>Ahead and behind counts are unavailable.</small></p>}
    {upstream?.is_remote && <p className="muted"><small>Comparison uses the remote branch state saved in local Git.</small></p>}
  </details>;
}

export function useRepositoryOverview(base: string, path: string | null, registeredRemote: string) {
  const [snapshot, setSnapshot] = useState<{
    base: string;
    path: string;
    registeredRemote: string;
    refresh: number;
    status: RepositoryStatus | null;
    error: string;
  } | null>(null);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    if (!path) {
      setSnapshot(null);
      return;
    }
    const controller = new AbortController();
    const request = { base, path, registeredRemote, refresh };
    api<RepositoryStatus>(`${base}/repository`, { signal: controller.signal })
      .then((status) => {
        if (!controller.signal.aborted) setSnapshot({ ...request, status, error: '' });
      })
      .catch((err: Error) => {
        if (!controller.signal.aborted) setSnapshot({ ...request, status: null, error: err.message });
      });
    return () => controller.abort();
  }, [base, path, registeredRemote, refresh]);

  const current = snapshot?.base === base && snapshot.path === path && snapshot.registeredRemote === registeredRemote
    ? snapshot : null;
  const loading = !!path && current?.refresh !== refresh;
  return {
    status: current?.status ?? null,
    error: loading ? '' : current?.error ?? '',
    loading,
    reload: () => setRefresh((value) => value + 1),
  };
}

export function RepositoryOverview({ path, status, error, loading, reload }: {
  path: string;
} & ReturnType<typeof useRepositoryOverview>) {
  const remote = status?.remote_repository;
  return (
    <div>
      <h4>Git repository</h4>
      <code className="repository">{path}</code>
      {remote && <div className="repository-remote">
        <small>Remote repository{remote.name && ` · ${remote.name}`}</small>
        <code className="repository">{remote.web_url
          ? <a href={remote.web_url} target="_blank" rel="noopener noreferrer">{remote.address}</a>
          : remote.address}</code>
      </div>}
      <div aria-busy={loading}>
        {loading && <Feedback>Reading repository…</Feedback>}
        <ErrorBox error={error} />
        {status && (
          <p>
            {status.branch === null ? 'Detached HEAD' : <>Branch: <code>{status.branch}</code></>}
            {typeof status.tracked_files === 'number' && ` · ${status.tracked_files.toLocaleString()} tracked file${status.tracked_files === 1 ? '' : 's'}`}
            {status.tracked_files === null && ' · Tracked file count unavailable'}
            {' · '}{!status.dirty ? 'Clean working tree' : status.tracked_changes === undefined || status.untracked_files === undefined
              ? 'Uncommitted changes'
              : <>
                {status.tracked_changes > 0 && `${status.tracked_changes} tracked file${status.tracked_changes === 1 ? '' : 's'} changed`}
                {status.tracked_changes > 0 && status.untracked_files > 0 && ' · '}
                {status.untracked_files > 0 && `${status.untracked_files} untracked file${status.untracked_files === 1 ? '' : 's'}`}
              </>}
          </p>
        )}
      </div>
      {status && (status.upstream !== undefined || status.upstream_unavailable) && <BranchSync status={status} />}
      {!!status?.branches?.length && <details className="repository-branches">
        <summary>Local branches ({status.branches.length}{status.branches_limited ? '+' : ''})</summary>
        <PagedItems label="Local branch pages" listClass="repository-branch-list" pageSize={10}>
          {status.branches.map(branch => <li key={branch}>
            <code>{branch}</code>{branch === status.branch && <span className="muted"> · Current</span>}
          </li>)}
        </PagedItems>
        {status.branches_limited && <p className="muted">Showing up to {status.branches.length} local branches, including the current branch when available.</p>}
      </details>}
      {status?.remote_branches && <details className="repository-branches">
        <summary>Remote branches ({status.remote_branches.length}{status.remote_branches_limited ? '+' : ''})</summary>
        {status.remote_branches.length > 0 ? <>
          <p className="muted">Remote branch names saved in the local Git repository.</p>
          <PagedItems label="Remote branch pages" listClass="repository-branch-list" pageSize={10}>
            {status.remote_branches.map(branch => <li key={branch}><code>{branch}</code></li>)}
          </PagedItems>
        </> : <p className="muted">No remote-tracking branches found in the local snapshot.</p>}
        {status.remote_branches_limited && <p className="muted">The snapshot shows only part of the remote branch list.</p>}
      </details>}
      <button className="secondary" type="button" disabled={loading} onClick={reload}>Refresh Git snapshot</button>
    </div>
  );
}


export function githubIssuesUrl(remote: string) {
  const match = /^(?:https:\/\/github\.com\/|ssh:\/\/git@github\.com\/|git@github\.com:)([a-z\d-]{1,39})\/([\w.-]{1,100}?)(?:\.git)?\/?$/i.exec(remote.trim());
  if (!match) return null;
  const repo = match[2];
  return repo && repo !== '.' && repo !== '..' ? `https://github.com/${match[1]}/${repo}/issues` : null;
}

export function GitHubIssues({ base, url }: { base: string; url: string }) {
  const [open, setOpen] = useState(false);
  const [issues, setIssues] = useState<{ number: number; title: string; url: string }[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setIssues(null);
    setError('');
    api<{ issues: { number: number; title: string; url: string }[] }>(
      `${base}/repository/issues`, { signal: controller.signal },
    ).then((result) => {
      if (!controller.signal.aborted) setIssues(result.issues);
    }).catch((e: Error) => {
      if (!controller.signal.aborted) setError(e.message);
    });
    return () => controller.abort();
  }, [base, open, url]);

  return (
    <details className="brief-section" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>Open GitHub issues</summary>
      {open && <>
        <ErrorBox error={error} />
        {!error && issues === null && <Feedback>Loading GitHub issues…</Feedback>}
        {issues?.length === 0 && <p>No open issues.</p>}
        {!!issues?.length && <ul>{issues.map((issue) => (
          <li key={issue.number}><a href={issue.url} target="_blank" rel="noopener noreferrer">
            #{issue.number} {issue.title}
          </a></li>
        ))}</ul>}
        <a href={url} target="_blank" rel="noopener noreferrer">View all on GitHub</a>
      </>}
    </details>
  );
}
