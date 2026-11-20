import { useEffect, useState } from 'react';
import { api } from './api';
import { generationProviderLabels, type GenerationProvider } from './generationProvider';

type ProfileOptions = { options: { id: string; label: string }[]; launch_label: string };
type Connection = { state: 'connected' | 'not_installed' | 'unavailable'; detail?: string };
const statusLabels = { connected: 'Signed in', not_installed: 'Not installed', unavailable: 'Unavailable' };

export function AIProfileConnections({ refreshVersion }: { refreshVersion: number }) {
  return <>
    <AIProfileConnection provider="codex" refreshVersion={refreshVersion} />
    <AIProfileConnection provider="claude" refreshVersion={refreshVersion} />
  </>;
}

function AIProfileConnection({ provider, refreshVersion }: { provider: GenerationProvider; refreshVersion: number }) {
  const [profiles, setProfiles] = useState<ProfileOptions | null>(null);
  const [profile, setProfile] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [optionsError, setOptionsError] = useState(false);
  const name = generationProviderLabels[provider];

  useEffect(() => {
    const request = new AbortController();
    api<ProfileOptions>(`/settings/ai-profiles?provider=${provider}`, { signal: request.signal })
      .then(result => {
        if (request.signal.aborted) return;
        if (!Array.isArray(result?.options)) throw new Error('Invalid profile list');
        setProfiles(result);
        setOptionsError(false);
      })
      .catch(() => { if (!request.signal.aborted) setOptionsError(true); });
    return () => request.abort();
  }, [provider, refreshVersion, attempt]);

  return <div className="compact-row">
    <strong>{name}</strong>
    {(profile || !!profiles?.options.length) && <label>
      Profile to check
      <select aria-label={`${name} profile to check`} value={profile} onChange={event => setProfile(event.target.value)}>
        <option value="">{profiles?.launch_label || 'Launch account'}</option>
        {profile && !profiles?.options.some(option => option.id === profile) &&
          <option value={profile}>Unavailable profile</option>}
        {profiles?.options.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
      </select>
    </label>}
    {optionsError && <p className="muted">Couldn’t load saved profiles.</p>}
    <AIConnectionStatus key={JSON.stringify([provider, profile, refreshVersion, attempt])} provider={provider} profile={profile}
      onRetry={() => setAttempt(value => value + 1)} />
  </div>;
}

function AIConnectionStatus({ provider, profile, onRetry }: {
  provider: GenerationProvider; profile: string; onRetry: () => void;
}) {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const request = new AbortController();
    const params = new URLSearchParams({ provider });
    if (profile) params.set('profile', profile);
    api<Connection>(`/settings/ai-profiles/status?${params}`, { signal: request.signal })
      .then(result => {
        if (request.signal.aborted) return;
        if (result && Object.hasOwn(statusLabels, result.state)) setConnection(result);
        else setFailed(true);
      })
      .catch(() => { if (!request.signal.aborted) setFailed(true); });
    return () => request.abort();
  }, [provider, profile]);

  return <>
    <p role="status" className="muted">
      {failed ? 'Couldn’t check connection.' : connection ? statusLabels[connection.state] : 'Checking connection…'}
      {connection?.detail && <> · {connection.detail}</>}
    </p>
    <button type="button" className="quiet" disabled={!failed && !connection}
      aria-label={`Check ${generationProviderLabels[provider]} connection`} onClick={onRetry}>Check again</button>
  </>;
}
