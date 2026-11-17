import { useEffect, useId, useRef, useState } from 'react';
import { api, patch } from './api';
import { ErrorBox } from './ui';
import { Feedback } from './Feedback';
import { SettingsHelp } from './SettingsHelp';
import { generationProfilePatch, parseAdvancedSchemeSettings, parseGenerationProfileSettings, parseSchemeSourceSettings, type GenerationProfileSettings } from './generationDiagnostics';

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

export function ExtractionWorkersControl({ active }: { active: boolean }) {
  const helpId = useId();
  const budgetHelpId = useId();
  const schemeHelpId = useId();
  const advancedSchemeHelpId = useId();
  const [settings, setSettings] = useState<WorkerSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState('');
  const [profileDraft, setProfileDraft] = useState('');
  const [budgetDraft, setBudgetDraft] = useState('');
  const [schemeDraft, setSchemeDraft] = useState('');
  const [advancedSchemeDraft, setAdvancedSchemeDraft] = useState<Partial<Record<AdvancedSchemeField, number>>>({});
  const [savedField, setSavedField] = useState<'workers' | 'profile' | 'budget' | 'scheme' | 'advancedScheme'>('workers');
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
          setProfileDraft(result.profileSettings?.generation_profile ?? '');
          setBudgetDraft(result.budgetSettings ? String(result.budgetSettings.value) : '');
          setSchemeDraft(result.schemeSourceSettings ? String(result.schemeSourceSettings.value) : '');
          setAdvancedSchemeDraft({});
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
    setSavedField('workers');
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

  async function saveProfile() {
    if (savingRef.current || !settings?.profileSettings) return;
    savingRef.current = true;
    setSavedField('profile');
    setSaving(true); setError(''); setSaved(false);
    try {
      const result = settingsResponse(await patch('/settings/ai', generationProfilePatch(profileDraft)));
      if (!result.profileSettings) throw new Error('The server did not return the saved generation profile.');
      if (mounted.current) {
        ++loadVersion.current;
        setSettings(result);
        setProfileDraft(result.profileSettings.generation_profile);
        setSaved(true);
      }
    } catch (failure: unknown) {
      if (mounted.current) {
        setProfileDraft(settings.profileSettings.generation_profile);
        setError(failure instanceof Error ? failure.message : 'Could not save generation profile.');
      }
    } finally {
      savingRef.current = false;
      if (mounted.current) setSaving(false);
    }
  }

  async function saveBudget() {
    const budget = settings?.budgetSettings;
    const seconds = Number(budgetDraft);
    if (savingRef.current || !budget || !Number.isSafeInteger(seconds) || seconds < budget.min || seconds > budget.max) return;
    savingRef.current = true;
    setSavedField('budget');
    setSaving(true); setError(''); setSaved(false);
    try {
      const result = settingsResponse(await patch('/settings/ai', { generation_budget: seconds }));
      if (!result.budgetSettings) throw new Error('The server did not return the saved time budget.');
      if (mounted.current) {
        ++loadVersion.current;
        setSettings(result);
        setBudgetDraft(String(result.budgetSettings.value));
        setSaved(true);
      }
    } catch (failure: unknown) {
      if (mounted.current) setError(failure instanceof Error ? failure.message : 'Could not save time budget.');
    } finally {
      savingRef.current = false;
      if (mounted.current) setSaving(false);
    }
  }

  async function saveSchemeBudget() {
    const characters = Number(schemeDraft);
    if (savingRef.current || !settings?.schemeSourceSettings?.options.includes(characters)) return;
    savingRef.current = true;
    setSavedField('scheme');
    setSaving(true); setError(''); setSaved(false);
    try {
      const result = settingsResponse(await patch('/settings/ai', { scheme_source_chars: characters }));
      if (!result.schemeSourceSettings) throw new Error('The server did not return the saved Scheme excerpt budget.');
      if (mounted.current) {
        ++loadVersion.current;
        setSettings(result);
        setSchemeDraft(String(result.schemeSourceSettings.value));
        setSaved(true);
      }
    } catch (failure: unknown) {
      if (mounted.current) setError(failure instanceof Error ? failure.message : 'Could not save the Scheme excerpt budget.');
    } finally {
      savingRef.current = false;
      if (mounted.current) setSaving(false);
    }
  }

  async function saveAdvancedScheme() {
    if (savingRef.current || !Object.keys(advancedSchemeChanges).length) return;
    savingRef.current = true;
    setSavedField('advancedScheme');
    setSaving(true); setError(''); setSaved(false);
    try {
      const result = settingsResponse(await patch('/settings/ai', advancedSchemeChanges));
      if (advancedSchemeFields.some(({ name }) => name in advancedSchemeChanges && !result.advancedSchemeSettings[name])) {
        throw new Error('The server did not return the saved advanced Scheme settings.');
      }
      if (mounted.current) {
        ++loadVersion.current;
        setSettings(result);
        setAdvancedSchemeDraft({});
        setSaved(true);
      }
    } catch (failure: unknown) {
      if (mounted.current) setError(failure instanceof Error ? failure.message : 'Could not save advanced Scheme settings.');
    } finally {
      savingRef.current = false;
      if (mounted.current) setSaving(false);
    }
  }

  const profileSettings = settings?.profileSettings;
  const profile = profileSettings?.generation_profiles.find((value) => value.id === profileDraft);
  const unchangedProfile = profileDraft === profileSettings?.generation_profile
    && profileDraft === profileSettings?.configured_generation_profile;
  const count = Number(draft);
  const valid = !!settings && Number.isSafeInteger(count)
    && count >= settings.min_extraction_workers && count <= settings.max_extraction_workers;
  const unchanged = count === settings?.extraction_workers && count === settings?.configured_extraction_workers;
  const budget = settings?.budgetSettings;
  const seconds = Number(budgetDraft);
  const validBudget = !!budget && Number.isSafeInteger(seconds) && seconds >= budget.min && seconds <= budget.max;
  const unchangedBudget = seconds === budget?.value && seconds === budget?.configured;
  const schemeBudget = settings?.schemeSourceSettings;
  const schemeCharacters = Number(schemeDraft);
  const unchangedScheme = schemeCharacters === schemeBudget?.value && schemeCharacters === schemeBudget?.configured;
  const advancedScheme = settings?.advancedSchemeSettings;
  const advancedSchemeChanges: Partial<Record<AdvancedSchemeField, number>> = {};
  for (const { name } of advancedSchemeFields) {
    const setting = advancedScheme?.[name];
    const value = advancedSchemeDraft[name];
    if (setting && value !== undefined && setting.options.includes(value)
      && (value !== setting.value || value !== setting.configured)) advancedSchemeChanges[name] = value;
  }
  const savedMessage = {
    workers: 'Thread count saved.', profile: 'Generation profile saved.',
    budget: 'Overall time budget saved.', scheme: 'Scheme excerpt budget saved.',
    advancedScheme: 'Advanced Scheme settings saved.',
  }[savedField];
  const saveFeedback = <>
    <ErrorBox error={error} />
    {error && !saving && <button type="button" className="secondary" onClick={() => setReload((value) => value + 1)}>
      Reload setting
    </button>}
    {(loading || saving || saved) && <Feedback tone={saved && !saving && !loading ? 'success' : 'info'}>
      {saving ? 'Saving…' : loading ? 'Loading AI settings…' : savedMessage}
    </Feedback>}
  </>;
  return <div>
    <label>
      Knowledge generation profile
      <select aria-label="Knowledge generation profile" value={profileDraft}
        disabled={loading || saving || !profileSettings}
        onChange={(event) => { setProfileDraft(event.target.value); setSaved(false); }}>
        {!profileSettings && <option value="">{loading ? 'Loading…' : 'Profiles unavailable'}</option>}
        {profileSettings?.generation_profiles.map((option) => <option key={option.id} value={option.id}>
          {option.label}{option.id === 'quality' ? ' (default)' : option.experimental ? ' (experimental)' : ''}
        </option>)}
      </select>
    </label>
    <SettingsHelp title="Profile explanation">
      <ul>
        <li><strong>Quality (default):</strong> Smaller batches to help preserve detail. Uses all available source material.</li>
        <li><strong>Balanced (experimental):</strong> Larger batches cover the same sources with potentially fewer calls, but may miss details.</li>
        <li><strong>Economy (experimental):</strong> Larger batches and a source limit can reduce usage, but some sources may be skipped.</li>
      </ul>
      <p>Your model and explicit reasoning choice stay the same. Economy uses low reasoning when supported if you haven’t chosen an effort.
        Changing profiles may require reprocessing; saved results stay intact.</p>
    </SettingsHelp>
    <div className="actions left">
      <button type="button" className="secondary" disabled={loading || saving || !profile || unchangedProfile}
        onClick={() => void saveProfile()}>Save generation profile</button>
    </div>
    {savedField === 'profile' && !loading && saveFeedback}
    {profileSettings && profileSettings.generation_profile !== profileSettings.configured_generation_profile
      && <p><small>Startup environment overrides the saved profile ({profileSettings.configured_generation_profile}).</small></p>}
    <label>
      AI workers per job
      <input type="number" min={settings?.min_extraction_workers ?? 1}
        max={settings?.max_extraction_workers} step={1}
        aria-label="AI workers per job" aria-describedby={helpId}
        aria-invalid={settings !== null && !valid}
        value={draft}
        disabled={loading || saving || settings === null}
        placeholder={loading ? 'Loading…' : 'Workers per job'}
        onChange={(event) => { setDraft(event.target.value); setSaved(false); }} />
    </label>
    <div className="actions left">
      <button type="button" className="secondary" disabled={loading || saving || !valid || unchanged}
        onClick={() => void save(count)}>Save workers per job</button>
    </div>
    {(savedField === 'workers' || loading) && saveFeedback}
    {settings && !valid && <Feedback>Enter a whole number from {settings.min_extraction_workers} to {settings.max_extraction_workers}.</Feedback>}
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
        aria-invalid={!!budget && !validBudget} value={budgetDraft}
        disabled={loading || saving || !budget}
        placeholder={loading ? 'Loading…' : budget ? '900' : 'Unavailable'}
        onChange={(event) => { setBudgetDraft(event.target.value); setSaved(false); }} />
    </label>
    <div className="actions left">
      <button type="button" className="secondary" disabled={loading || saving || !validBudget || unchangedBudget}
        onClick={() => void saveBudget()}>Save time budget</button>
    </div>
    {savedField === 'budget' && !loading && saveFeedback}
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
        <select aria-describedby={schemeHelpId} value={schemeDraft} disabled={loading || saving}
          onChange={(event) => { setSchemeDraft(event.target.value); setSaved(false); }}>
          {schemeBudget.options.map(option => <option key={option} value={option}>
            {option.toLocaleString()} characters{option === 40_000 ? ' (default)' : ''}
          </option>)}
        </select>
      </label>
      <SettingsHelp title="About the Scheme excerpt budget" id={schemeHelpId}>
        <p>Maximum text selected from code and other sources for the next Scheme generation. For larger projects, try 60,000 characters. Try 80,000 if useful files are still skipped because of this budget. Larger inputs can use more tokens and take longer.</p>
        <p>Local file reads and the final evidence package have their own limits.</p>
      </SettingsHelp>
      <div className="actions left">
        <button type="button" className="secondary"
          disabled={loading || saving || !schemeBudget.options.includes(schemeCharacters) || unchangedScheme}
          onClick={() => void saveSchemeBudget()}>Save Scheme excerpt budget</button>
      </div>
      {savedField === 'scheme' && !loading && saveFeedback}
      {schemeBudget.value !== schemeBudget.configured
        && <p><small>Startup environment overrides the saved excerpt budget ({schemeBudget.configured.toLocaleString()} characters).</small></p>}
    </>}
    {advancedScheme && advancedSchemeFields.some(({ name }) => advancedScheme[name]) && <SettingsHelp title="Advanced Scheme settings">
      <p id={advancedSchemeHelpId}>These limits apply to the next Scheme inspection. The excerpt budget still controls how much source text Scheme selects.</p>
      {advancedSchemeFields.map(({ name, label, format, help }) => {
        const setting = advancedScheme[name];
        if (!setting) return null;
        return <div key={name}>
          <label>
            {label}
            <select aria-describedby={advancedSchemeHelpId} value={advancedSchemeDraft[name] ?? setting.value}
              disabled={loading || saving} onChange={(event) => {
                const value = Number(event.target.value);
                setAdvancedSchemeDraft(previous => ({ ...previous, [name]: value }));
                setSaved(false);
              }}>
              {setting.options.map((option, index) => <option key={option} value={option}>
                {format(option)}{index === 0 ? ' (default)' : ''}
              </option>)}
            </select>
          </label>
          <p><small>{help}</small></p>
          {setting.value !== setting.configured
            && <p><small>Startup environment overrides the saved limit ({format(setting.configured)}).</small></p>}
        </div>;
      })}
      <div className="actions left">
        <button type="button" className="secondary"
          disabled={loading || saving || !Object.keys(advancedSchemeChanges).length}
          onClick={() => void saveAdvancedScheme()}>Save advanced Scheme settings</button>
      </div>
      {savedField === 'advancedScheme' && !loading && saveFeedback}
    </SettingsHelp>}
  </div>;
}
