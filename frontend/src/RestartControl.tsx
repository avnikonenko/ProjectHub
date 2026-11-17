import { useEffect, useRef, useState } from 'react';
import { Feedback } from './Feedback';
import { ErrorBox } from './ui';
import { readRestartStatus, restartProjectHub, type RestartStatus } from './restart';

export function RestartControl({ active }: { active: boolean }) {
  const [status, setStatus] = useState<RestartStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const operation = useRef<AbortController | null>(null);
  const busyRef = useRef(false);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    operation.current = controller;
    setLoading(true);
    setStatus(null);
    setError('');
    setConnected(false);
    void readRestartStatus(controller.signal).then(value => {
      if (!controller.signal.aborted) setStatus(value);
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Could not load restart status.');
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => {
      controller.abort();
      operation.current?.abort();
      busyRef.current = false;
    };
  }, [active, reload]);

  async function restart() {
    if (!active || busyRef.current || loading || !status?.available || status.restarting) return;
    operation.current?.abort();
    const controller = new AbortController();
    operation.current = controller;
    busyRef.current = true;
    setBusy(true);
    setError('');
    setConnected(false);
    try {
      const ready = await restartProjectHub(status.instance_id, controller.signal);
      if (controller.signal.aborted) return;
      setStatus(ready);
      setConnected(true);
    } catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure instanceof Error ? failure.message : 'Could not restart ProjectHub.');
      }
    } finally {
      if (!controller.signal.aborted) { busyRef.current = false; setBusy(false); }
    }
  }

  // Closing Settings cancels polling. A subsequent open starts a fresh status read.
  useEffect(() => { if (!active) { setBusy(false); setLoading(false); } }, [active]);

  return <div>
    <button type="button" className="secondary"
      disabled={!active || loading || busy || !status?.available || status.restarting}
      onClick={() => void restart()}>{busy ? 'Restarting…' : 'Restart ProjectHub'}</button>
    {(loading || busy || connected) && <Feedback tone={connected ? 'success' : 'info'}>
      {connected ? 'ProjectHub restarted and reconnected.' : busy ? 'Restarting… Waiting for ProjectHub to reconnect.' : 'Loading restart status…'}
    </Feedback>}
    {connected && <button type="button" className="secondary" onClick={() => window.location.reload()}>
      Reload page
    </button>}
    {!loading && !busy && status?.reason && <Feedback>{status.reason}</Feedback>}
    <ErrorBox error={error} />
    {error && !busy && <button type="button" className="secondary" onClick={() => setReload(value => value + 1)}>
      Reload restart status
    </button>}
  </div>;
}
