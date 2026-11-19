import { useEffect, useId, useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react';
import { api, patch } from './api';
import { ErrorBox } from './ui';
import { Feedback } from './Feedback';
import { SettingsHelp } from './SettingsHelp';
import { parseAdvancedSchemeSettings, parseGenerationProfileSettings, parseSchemeSourceSettings, type GenerationProfileSettings } from './generationDiagnostics';

type WorkerSettings = {
  extraction_workers: number;
  configured_extraction_workers: number;
  min_extraction_workers: number;
  max_extraction_workers: number;
  profileSettings?: GenerationProfileSettings | null;
  budgetSettings?: { value: number; configured: number; min: number; max: number } | null;
  schemeSourceSettings?: ReturnType<typeof parseSchemeSourceSettings>;
  advancedSchemeSettings: ReturnType<typeof parseAdvancedSchemeSettings>;
};

const advancedSchemeFields = [
  {
    name: 'scheme_read_bytes',
    label: 'Local file read limit',
    format: (value: number) => `${value / (1024 * 1024)} MiB`,
    help: 'Limits total repository data read from disk for Scheme. A higher limit can increase inspection time.',
  },
  {
    name: 'scheme_package_chars',
    label: 'Final evidence package limit',
    format: (value: number) => `${value.toLocaleString()} characters`,
    help: 'Limits the serialized Scheme evidence, including excerpts, dependencies, and the file tree. Larger packages may use more AI tokens.',
  },
] as const;
type AdvancedSchemeField = typeof advancedSchemeFields[number]['name'];

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
  const budget = value as Record<string, unknown>;
  let budgetSettings = null;
  if ('generation_budget' in budget || 'configured_generation_budget' in budget) {
    const min = budget.min_generation_budget;
    const max = budget.max_generation_budget;
    const effective = budget.generation_budget;
    const configured = budget.configured_generation_budget;
    if (!validCount(min) || !validCount(max) || !validCount(effective) || !validCount(configured)
      || Number(min) > Number(max) || Number(effective) < Number(min) || Number(effective) > Number(max)
      || Number(configured) < Number(min) || Number(configured) > Number(max)) {
      throw new Error('AI settings returned an unsupported time budget.');
    }
    budgetSettings = { value: Number(effective), configured: Number(configured), min: Number(min), max: Number(max) };
  }
  return { ...settings, profileSettings: parseGenerationProfileSettings(value), budgetSettings,
    schemeSourceSettings: parseSchemeSourceSettings(value), advancedSchemeSettings: parseAdvancedSchemeSettings(value) } as WorkerSettings;
}

type SettingField = 'extraction_workers' | 'generation_profile' | 'generation_budget' | 'scheme_source_chars' | AdvancedSchemeField;
type SettingOption = string | number;
type Setting = { value: SettingOption; configured: SettingOption } &
  ({ min: number; max: number } | { options: readonly SettingOption[] });
type SettingEdit = { field: SettingField; draft: string };
type SaveNotice = { state: 'saving' | 'saved' } | { state: 'failed'; error: string };
const savedMessages: Record<SettingField, string> = {
  extraction_workers: 'Thread count saved.',
  generation_profile: 'Generation profile saved.',
  generation_budget: 'Overall time budget saved.',
  scheme_source_chars: 'Scheme excerpt budget saved.',
  scheme_read_bytes: 'Local file read limit saved.',
  scheme_package_chars: 'Final evidence package limit saved.',
};

function settingFor(settings: WorkerSettings | null, field: SettingField): Setting | null | undefined {
  if (!settings) return null;
  switch (field) {
    case 'extraction_workers':
      return { value: settings.extraction_workers, configured: settings.configured_extraction_workers,
        min: settings.min_extraction_workers, max: settings.max_extraction_workers };
    case 'generation_profile': {
      const profile = settings.profileSettings;
      return profile && { value: profile.generation_profile, configured: profile.configured_generation_profile,
        options: profile.generation_profiles.map(option => option.id) };
    }
    case 'generation_budget': return settings.budgetSettings;
    case 'scheme_source_chars': return settings.schemeSourceSettings;
    default: return settings.advancedSchemeSettings[field];
  }
}

function settingValue(setting: Setting | null | undefined, draft: string) {
  if (!setting || !draft.trim()) return undefined;
  const value = typeof setting.value === 'number' ? Number(draft) : draft;
  if ('options' in setting) return setting.options.includes(value) ? value : undefined;
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= setting.min && value <= setting.max
    ? value : undefined;
}

export function ExtractionWorkersControl({ active, refreshVersion = 0 }: { active: boolean; refreshVersion?: number }) {
  const helpId = useId();
  const budgetHelpId = useId();
  const schemeHelpId = useId();
  const advancedSchemeHelpId = useId();
  const [settings, setSettings] = useState<WorkerSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [reload, setReload] = useState(0);
  const [drafts, setDrafts] = useState<Partial<Record<SettingField, string>>>({});
  const [notices, setNotices] = useState<Partial<Record<SettingField, SaveNotice>>>({});
  const mounted = useRef(true);
  const currentSettings = useRef<WorkerSettings | null>(null);
  const loadVersion = useRef(0);
  const latestEdits = useRef<Partial<Record<SettingField, SettingEdit>>>({});
  const timers = useRef(new Map<SettingField, ReturnType<typeof setTimeout>>());
  const writeQueue = useRef(Promise.resolve());

  useEffect(() => {
    mounted.current = true;
    const pendingTimers = timers.current;
    return () => {
      mounted.current = false;
      pendingTimers.forEach(clearTimeout);
      pendingTimers.clear();
    };
  }, []);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    const version = ++loadVersion.current;
    setLoading(true);
    setLoadError('');
    api<unknown>('/settings/ai', { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted && version === loadVersion.current) {
          const result = settingsResponse(value);
          currentSettings.current = result;
          setSettings(result);
        }
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted && version === loadVersion.current) {
          setLoadError(failure instanceof Error ? failure.message : 'Could not load AI settings.');
        }
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [active, refreshVersion, reload]);

  function enqueueSave(edit: SettingEdit) {
    const { field, draft } = edit;
    setNotices(previous => ({ ...previous, [field]: { state: 'saving' } }));
    // PATCH replies include all settings. Applying them in order keeps the latest
    // saved settings independent of the order in which the user edits fields.
    writeQueue.current = writeQueue.current.then(async () => {
      if (!mounted.current || latestEdits.current[field] !== edit) return;
      const setting = settingFor(currentSettings.current, field);
      const value = settingValue(setting, draft);
      try {
        if (value === undefined) throw new Error('The permitted values changed. Choose another value.');
        if (setting?.value === value && setting.configured === value) {
          setNotices(previous => ({ ...previous, [field]: undefined }));
        } else {
          const result = settingsResponse(await patch('/settings/ai', { [field]: value }));
          if (!mounted.current) return;
          if (settingFor(result, field)?.configured !== value) {
            throw new Error('The server did not confirm the new value.');
          }
          ++loadVersion.current;
          currentSettings.current = result;
          setSettings(result);
          if (latestEdits.current[field] === edit) {
            setNotices(previous => ({ ...previous, [field]: { state: 'saved' } }));
          }
        }
        if (latestEdits.current[field] === edit) {
          delete latestEdits.current[field];
          setDrafts(previous => ({ ...previous, [field]: undefined }));
        }
      } catch (failure: unknown) {
        if (mounted.current && latestEdits.current[field] === edit) {
          const error = failure instanceof Error ? failure.message : 'The setting could not be saved.';
          setNotices(previous => ({ ...previous, [field]: { state: 'failed', error } }));
        }
      }
    });
  }

  function finishNumber(field: SettingField) {
    if (!timers.current.has(field)) return;
    clearTimeout(timers.current.get(field));
    timers.current.delete(field);
    const edit = latestEdits.current[field];
    if (edit) enqueueSave(edit);
  }

  function changeSetting(field: SettingField, draft: string, numericInput = false) {
    clearTimeout(timers.current.get(field));
    timers.current.delete(field);
    const edit = { field, draft };
    latestEdits.current[field] = edit;
    setDrafts(previous => ({ ...previous, [field]: draft }));
    setNotices(previous => ({ ...previous, [field]: undefined }));
    if (settingValue(settingFor(currentSettings.current, field), draft) === undefined) return;
    if (numericInput) {
      timers.current.set(field, setTimeout(() => finishNumber(field), 550));
    } else {
      enqueueSave(edit);
    }
  }

  function inputValue(field: SettingField) {
    const value = settingFor(settings, field)?.value;
    return drafts[field] ?? (value === undefined ? '' : String(value));
  }
  function numericEvents(field: SettingField) {
    return {
      onChange: (event: ChangeEvent<HTMLInputElement>) => changeSetting(field, event.target.value, true),
      onBlur: () => finishNumber(field),
      onKeyDown: (event: KeyboardEvent<HTMLInputElement>) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        finishNumber(field);
      },
    };
  }
  function saveFeedback(field: SettingField) {
    const notice = notices[field];
    if (notice?.state === 'saving') return <Feedback>Saving…</Feedback>;
    if (notice?.state === 'saved') return <Feedback tone="success">{savedMessages[field]}</Feedback>;
    if (notice?.state === 'failed') return <>
      <ErrorBox error={`Could not save this setting. ${notice.error}`} />
      <button type="button" className="secondary" disabled={loading} onClick={() => {
        const edit = latestEdits.current[field];
        if (edit) enqueueSave(edit);
      }}>Retry save</button>
    </>;
    return null;
  }

  const profileSettings = settings?.profileSettings;
  const budget = settings?.budgetSettings;
  const schemeBudget = settings?.schemeSourceSettings;
  const advancedScheme = settings?.advancedSchemeSettings;
  const validWorkers = settingValue(settingFor(settings, 'extraction_workers'), inputValue('extraction_workers')) !== undefined;
  const validBudget = settingValue(budget, inputValue('generation_budget')) !== undefined;
  return <div>
    {loading && <Feedback>Loading AI settings…</Feedback>}
    <ErrorBox error={loadError} />
    {!!loadError && <button type="button" className="secondary" onClick={() => setReload(value => value + 1)}>
      Reload AI settings
    </button>}
    <label>
      AI generation profile
      <select aria-label="AI generation profile" value={inputValue('generation_profile')}
        disabled={loading || !profileSettings}
        onChange={event => changeSetting('generation_profile', event.target.value)}>
        {!profileSettings && <option value="">{loading ? 'Loading…' : 'Profiles unavailable'}</option>}
        {profileSettings?.generation_profiles.map((option) => <option key={option.id} value={option.id}>
          {option.label}{option.id === 'quality' ? ' (default)' : option.experimental ? ' (experimental)' : ''}
        </option>)}
      </select>
    </label>
    <SettingsHelp title="Profile explanation">
      <ul>
        <li><strong>Quality (default).</strong> Knowledge reads available original messages in smaller batches to preserve detail.</li>
        <li><strong>Balanced (experimental).</strong> Knowledge reads available original messages in larger batches, which can reduce calls.</li>
        <li><strong>Economy (experimental).</strong> Reuses saved conversation overviews across reviews. Knowledge checks selected original messages.</li>
      </ul>
      <p>Your model and explicit reasoning choice stay the same. Economy uses low reasoning when your model supports it and you leave effort unset.
        The first Economy run needs to build conversation overviews. Later runs reuse matching batches.</p>
    </SettingsHelp>
    {saveFeedback('generation_profile')}
    {profileSettings && profileSettings.generation_profile !== profileSettings.configured_generation_profile
      && <p><small>Startup environment overrides the saved profile ({profileSettings.configured_generation_profile}).</small></p>}
    <label>
      AI workers per job
      <input type="number" min={settings?.min_extraction_workers ?? 1}
        max={settings?.max_extraction_workers} step={1}
        aria-label="AI workers per job" aria-describedby={helpId}
        aria-invalid={settings !== null && !validWorkers}
        value={inputValue('extraction_workers')}
        disabled={loading || settings === null}
        placeholder={loading ? 'Loading…' : 'Workers per job'}
        {...numericEvents('extraction_workers')} />
    </label>
    {saveFeedback('extraction_workers')}
    {settings && !validWorkers && <Feedback>Enter a whole number from {settings.min_extraction_workers} to {settings.max_extraction_workers}.</Feedback>}
    <SettingsHelp title="Worker-count explanation" id={helpId}>
      <p>Maximum parallel AI batches for each generation job; 1 is serial, 10 is the default. Applies to the next job.
        {' '}Concurrent jobs each get this limit, so total memory use and account requests can increase.</p>
    </SettingsHelp>
    {settings && settings.extraction_workers !== settings.configured_extraction_workers
      && <p><small>Startup environment overrides the saved value ({settings.configured_extraction_workers}).</small></p>}
    <label>
      Overall time budget (seconds)
      <input type="number" min={budget?.min ?? 30} max={budget?.max ?? 7200} step={1}
        aria-label="Overall time budget (seconds)" aria-describedby={budgetHelpId}
        aria-invalid={!!budget && !validBudget} value={inputValue('generation_budget')}
        disabled={loading || !budget}
        placeholder={loading ? 'Loading…' : budget ? '900' : 'Unavailable'}
        {...numericEvents('generation_budget')} />
    </label>
    {saveFeedback('generation_budget')}
    {budget && !validBudget && <Feedback>Enter a whole number from {budget.min} to {budget.max} seconds.</Feedback>}
    <SettingsHelp title="About the overall time budget" id={budgetHelpId}>
      <p>Total time allowed across a generation's stages; default 900 seconds (15 minutes).
        {' '}Applies to the next generation. Per-response timeouts still apply.</p>
      <p>For Project status, AI Q&A, Scheme, Knowledge and Ask Project. Codex discussions are separate.</p>
    </SettingsHelp>
    {budget && budget.value !== budget.configured
      && <p><small>Startup environment overrides the saved time budget ({budget.configured} seconds).</small></p>}
    {schemeBudget && <>
      <label>
        Scheme excerpt budget
        <select aria-describedby={schemeHelpId} value={inputValue('scheme_source_chars')} disabled={loading}
          onChange={event => changeSetting('scheme_source_chars', event.target.value)}>
          {schemeBudget.options.map(option => <option key={option} value={option}>
            {option.toLocaleString()} characters{option === 40_000 ? ' (default)' : ''}
          </option>)}
        </select>
      </label>
      <SettingsHelp title="About the Scheme excerpt budget" id={schemeHelpId}>
        <p>Maximum text selected from code and other sources for the next Scheme generation. For larger projects, try 60,000 characters. Try 80,000 if useful files are still skipped because of this budget. Larger inputs can use more tokens and take longer.</p>
        <p>Local file reads and the final evidence package have their own limits.</p>
      </SettingsHelp>
      {saveFeedback('scheme_source_chars')}
      {schemeBudget.value !== schemeBudget.configured
        && <p><small>Startup environment overrides the saved excerpt budget ({schemeBudget.configured.toLocaleString()} characters).</small></p>}
    </>}
    {advancedScheme && advancedSchemeFields.some(({ name }) => advancedScheme[name]) && <SettingsHelp title="Advanced Scheme settings">
      <p id={advancedSchemeHelpId}>Saved limits apply to the next Scheme inspection. The excerpt budget still controls how much source text Scheme selects.</p>
      {advancedSchemeFields.map(({ name, label, format, help }) => {
        const setting = advancedScheme[name];
        if (!setting) return null;
        return <div key={name}>
          <label>
            {label}
            <select aria-describedby={advancedSchemeHelpId} value={inputValue(name)}
              disabled={loading} onChange={event => changeSetting(name, event.target.value)}>
              {setting.options.map((option, index) => <option key={option} value={option}>
                {format(option)}{index === 0 ? ' (default)' : ''}
              </option>)}
            </select>
          </label>
          {saveFeedback(name)}
          <p><small>{help}</small></p>
          {setting.value !== setting.configured
            && <p><small>Startup environment overrides the saved limit ({format(setting.configured)}).</small></p>}
        </div>;
      })}
    </SettingsHelp>}
  </div>;
}
