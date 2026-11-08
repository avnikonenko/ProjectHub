import { useEffect, useState } from 'react';
import { api } from './api';
import { ErrorBox } from './ui';

type RepositoryStatus = {
  branch: string | null;
  dirty: boolean;
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
        {loading && <p role="status">Reading repository…</p>}
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
