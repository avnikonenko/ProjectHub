import { useEffect, useId, useRef, useState } from 'react';
import { api, patch } from './api';
import { ErrorBox } from './ui';

type WorkerSettings = {
  extraction_workers: number;
  configured_extraction_workers: number;
  min_extraction_workers: number;
  max_extraction_workers: number;
};

function settingsResponse(value: unknown): WorkerSettings {
  const settings = value as Partial<WorkerSettings> | null;
  const validCount = (count: unknown) => typeof count === 'number'
    && Number.isInteger(count) && count >= 1 && count <= 4;
  if (!settings || !validCount(settings.extraction_workers)
    || !validCount(settings.configured_extraction_workers)
    || settings.min_extraction_workers !== 1 || settings.max_extraction_workers !== 4) {
    throw new Error('AI settings returned an unsupported worker count.');
  }
  return settings as WorkerSettings;
}

export function ExtractionWorkersControl({ active }: { active: boolean }) {
  const helpId = useId();
  const [settings, setSettings] = useState<WorkerSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [pending, setPending] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [reload, setReload] = useState(0);
  const mounted = useRef(true);
  const savingRef = useRef(false);
  const loadVersion = useRef(0);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    const version = ++loadVersion.current;
    let abandoned = false;
    setLoading(true);
    setError('');
    setSaved(false);
    api<unknown>('/settings/ai', { signal: controller.signal })
      .then((value) => {
        if (!abandoned && version === loadVersion.current) setSettings(settingsResponse(value));
      })
      .catch((failure: unknown) => {
        if (!abandoned && version === loadVersion.current) {
          setError(failure instanceof Error ? failure.message : 'Could not load AI settings.');
        }
      })
      .finally(() => { if (!abandoned) setLoading(false); });
    return () => {
      abandoned = true;
      controller.abort();
    };
  }, [active, reload]);

  async function save(count: number) {
    if (savingRef.current || !Number.isInteger(count) || count < 1 || count > 4) return;
    savingRef.current = true;
    setSaving(true);
    setPending(count);
    setError('');
    setSaved(false);
    try {
      const result = settingsResponse(await patch('/settings/ai', { extraction_workers: count }));
      if (mounted.current) {
        // An older GET must not overwrite a mutation that has now completed.
        ++loadVersion.current;
        setSettings(result);
        setSaved(true);
      }
    } catch (failure: unknown) {
      if (mounted.current) setError(failure instanceof Error ? failure.message : 'Could not save AI settings.');
    } finally {
      savingRef.current = false;
      if (mounted.current) {
        setSaving(false);
        setPending(null);
      }
    }
  }

  return <div>
    <label>
      Parallel extraction workers
      <select aria-label="Parallel extraction workers" aria-describedby={helpId}
        value={pending ?? settings?.extraction_workers ?? ''}
        disabled={loading || saving || settings === null}
        onChange={(event) => { void save(Number(event.target.value)); }}>
        {!settings && <option value="">{loading ? 'Loading…' : 'Unavailable'}</option>}
        {[1, 2, 3, 4].map((count) => <option key={count} value={count}>
          {count}{count === 1 ? ' (serial)' : count === 4 ? ' (default)' : ''}
        </option>)}
      </select>
    </label>
    <small id={helpId}>For knowledge extraction. More workers use more memory and may hit account limits.
      {' '}Applies to the next generation.</small>
    {settings && settings.extraction_workers !== settings.configured_extraction_workers
      && <p><small>Startup environment overrides the saved value ({settings.configured_extraction_workers}).</small></p>}
    <ErrorBox error={error} />
    {error && !saving && <button type="button" className="secondary" onClick={() => setReload((value) => value + 1)}>
      Reload setting
    </button>}
    {(loading || saving || saved) && <p role="status"><small>{saving ? 'Saving…' : loading ? 'Loading AI settings…' : 'Saved.'}</small></p>}
  </div>;
}
