import { useEffect, useState } from 'react';
import { api } from './api';
import { ErrorBox } from './ui';

type RepositoryStatus = {
  branch: string | null;
  dirty: boolean;
  commits: { hash: string; subject: string }[];
};

export function RepositoryOverview({ base, path }: { base: string; path: string }) {
  const [status, setStatus] = useState<RepositoryStatus | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api<RepositoryStatus>(`${base}/repository`, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setStatus(result);
      })
      .catch((err: Error) => {
        if (!controller.signal.aborted) setError(err.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [base, refresh]);

  return (
    <div>
      <h4>Git repository</h4>
      <code className="repository">{path}</code>
      <div aria-busy={loading}>
        {loading && <p role="status">Reading repository…</p>}
        <ErrorBox error={error} />
        {status && (
          <>
            <p>
              {status.branch === null ? 'Detached HEAD' : <>Branch: <code>{status.branch}</code></>}
              {' · '}{status.dirty ? 'Uncommitted changes' : 'Clean working tree'}
            </p>
            {status.commits.length ? (
              <>
                <h4>Recent commits</h4>
                <ul>
                  {status.commits.slice(0, 3).map(({ hash, subject }) => (
                    <li key={hash}><code>{hash.slice(0, 8)}</code> {subject || '(no subject)'}</li>
                  ))}
                </ul>
              </>
            ) : <p>No commits yet.</p>}
          </>
        )}
      </div>
      <button className="secondary" type="button" disabled={loading} onClick={() => {
        setStatus(null);
        setError('');
        setLoading(true);
        setRefresh((value) => value + 1);
      }}>Refresh Git snapshot</button>
    </div>
  );
}
