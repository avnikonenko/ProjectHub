import { Feedback } from './Feedback';
import { useEffect, useState } from 'react';
import { api } from './api';
import { ErrorBox } from './ui';

type RepositoryStatus = {
  branch: string | null;
  dirty: boolean;
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
            {' · '}{status.dirty ? 'Uncommitted changes' : 'Clean working tree'}
          </p>
        )}
      </div>
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
