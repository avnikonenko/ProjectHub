import { Feedback } from './Feedback';
import { useEffect, useState } from 'react';
import { api } from './api';
import { ErrorBox, PagedItems } from './ui';

type RepositoryStatus = {
  branch: string | null;
  dirty: boolean;
  branches?: string[];
  branches_limited?: boolean;
  remote_branches?: string[];
  remote_branches_limited?: boolean;
  tracked_changes?: number;
  untracked_files?: number;
  github_issues_url?: string | null;
  commits: { hash: string; subject: string; committed_at?: string }[];
};

export function useRepositoryOverview(base: string, path: string | null) {
  const [snapshot, setSnapshot] = useState<{
    path: string;
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
    api<RepositoryStatus>(`${base}/repository`, { signal: controller.signal })
      .then((status) => {
        if (!controller.signal.aborted) setSnapshot({ path, status, error: '' });
      })
      .catch((err: Error) => {
        if (!controller.signal.aborted) setSnapshot({ path, status: null, error: err.message });
      });
    return () => controller.abort();
  }, [base, path, refresh]);

  const current = snapshot?.path === path ? snapshot : null;
  return {
    status: current?.status ?? null,
    error: current?.error ?? '',
    loading: !!path && !current,
    reload: () => {
      setSnapshot(null);
      setRefresh((value) => value + 1);
    },
  };
}

export function RepositoryOverview({ path, status, error, loading, reload }: {
  path: string;
} & ReturnType<typeof useRepositoryOverview>) {
  return (
    <div>
      <h4>Git repository</h4>
      <code className="repository">{path}</code>
      <div aria-busy={loading}>
        {loading && <Feedback>Reading repository…</Feedback>}
        <ErrorBox error={error} />
        {status && (
          <p>
            {status.branch === null ? 'Detached HEAD' : <>Branch: <code>{status.branch}</code></>}
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
