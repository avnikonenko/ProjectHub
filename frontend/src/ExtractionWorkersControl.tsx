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
    && Number.isSafeInteger(count) && count >= 1;
  if (!settings || !validCount(settings.extraction_workers)
    || !validCount(settings.configured_extraction_workers)
    || settings.min_extraction_workers !== 1 || !validCount(settings.max_extraction_workers)
    || settings.extraction_workers! > settings.max_extraction_workers!
    || settings.configured_extraction_workers! > settings.max_extraction_workers!) {
    throw new Error('AI settings returned an unsupported worker count.');
  }
  return settings as WorkerSettings;
}

export function ExtractionWorkersControl({ active }: { active: boolean }) {
  const helpId = useId();
  const [settings, setSettings] = useState<WorkerSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState('');
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
        if (!abandoned && version === loadVersion.current) {
          const result = settingsResponse(value);
          setSettings(result);
          setDraft(String(result.extraction_workers));
        }
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
    if (savingRef.current || !settings || !Number.isSafeInteger(count)
      || count < settings.min_extraction_workers || count > settings.max_extraction_workers) return;
    savingRef.current = true;
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      const result = settingsResponse(await patch('/settings/ai', { extraction_workers: count }));
      if (mounted.current) {
        // An older GET must not overwrite a mutation that has now completed.
        ++loadVersion.current;
        setSettings(result);
        setDraft(String(result.extraction_workers));
        setSaved(true);
      }
    } catch (failure: unknown) {
      if (mounted.current) setError(failure instanceof Error ? failure.message : 'Could not save AI settings.');
    } finally {
      savingRef.current = false;
      if (mounted.current) {
        setSaving(false);
      }
    }
  }

  const count = Number(draft);
  const valid = !!settings && Number.isSafeInteger(count)
    && count >= settings.min_extraction_workers && count <= settings.max_extraction_workers;
  const unchanged = count === settings?.extraction_workers && count === settings?.configured_extraction_workers;
  return <div>
    <label>
      Parallel AI reading threads
      <input type="number" min={settings?.min_extraction_workers ?? 1}
        max={settings?.max_extraction_workers} step={1}
        aria-label="Parallel AI reading threads" aria-describedby={helpId}
        aria-invalid={settings !== null && !valid}
        value={draft}
        disabled={loading || saving || settings === null}
        placeholder={loading ? 'Loading…' : 'Number of threads'}
        onChange={(event) => { setDraft(event.target.value); setSaved(false); }} />
    </label>
    <div className="actions left">
      <button type="button" className="secondary" disabled={loading || saving || !valid || unchanged}
        onClick={() => void save(count)}>Save thread count</button>
    </div>
    <p><small id={helpId}>For conversation extraction; 1 is serial, 4 is the default. Applies to the next generation.
      {' '}More threads use more memory and may hit account limits.</small></p>
    {settings && !valid && <p role="status"><small>Enter a whole number from {settings.min_extraction_workers} to {settings.max_extraction_workers}.</small></p>}
    {settings && settings.extraction_workers !== settings.configured_extraction_workers
      && <p><small>Startup environment overrides the saved value ({settings.configured_extraction_workers}).</small></p>}
    <ErrorBox error={error} />
    {error && !saving && <button type="button" className="secondary" onClick={() => setReload((value) => value + 1)}>
      Reload setting
    </button>}
    {(loading || saving || saved) && <p role="status"><small>{saving ? 'Saving…' : loading ? 'Loading AI settings…' : 'Saved.'}</small></p>}
  </div>;
}
