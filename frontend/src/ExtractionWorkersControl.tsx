import { useEffect, useId, useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react';
import { api, patch } from './api';
import { ErrorBox } from './ui';
import { Feedback } from './Feedback';
import { SettingsHelp } from './SettingsHelp';
import { generationProfileLabels, parseAdvancedSchemeSettings, parseGenerationProfileSettings, parseSchemeSourceSettings, type GenerationProfileSettings } from './generationDiagnostics';

type WorkerSettings = {
  extraction_workers: number;
  configured_extraction_workers: number;
  selected_extraction_workers: number;
  min_extraction_workers: number;
  max_extraction_workers: number;
  profileSettings?: GenerationProfileSettings | null;
  budgetSettings?: { value: number; configured: number; min: number; max: number } | null;
  tokenLimitSettings?: { value: number; configured: number; min: number; max: number } | null;
  schemeSourceSettings?: ReturnType<typeof parseSchemeSourceSettings>;
  schemeNodeLimitSettings?: { value: number; configured: number; min: number; max: number } | null;
  advancedSchemeSettings: ReturnType<typeof parseAdvancedSchemeSettings>;
};

type AutoWorkerStatus = { target: number; active: number; waiting: number; ceiling: number };

function autoWorkerStatusResponse(value: unknown): AutoWorkerStatus | null {
  if (!value || typeof value !== 'object') return null;
  const { target, active, waiting, ceiling } = value as Partial<AutoWorkerStatus>;
  const count = (item: unknown, minimum: number): item is number =>
    typeof item === 'number' && Number.isSafeInteger(item) && item >= minimum;
  return count(target, 1) && count(ceiling, target) && count(active, 0) && count(waiting, 0)
    ? { target, active, waiting, ceiling }
    : null;
}

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
  const validCount = (count: unknown, minimum = 1): count is number => typeof count === 'number'
    && Number.isSafeInteger(count) && count >= minimum;
  const minimum = settings?.min_extraction_workers;
  const configured = settings?.configured_extraction_workers;
  const max = settings?.max_extraction_workers;
  const effective = settings?.extraction_workers;
  // An older server has only fixed counts and returns the selected count as the effective count.
  const selected = minimum === 1 ? effective : settings?.selected_extraction_workers;
  if (!settings || (minimum !== 0 && minimum !== 1)
    || !validCount(effective) || !validCount(max)
    || !validCount(configured, minimum) || !validCount(selected, minimum)
    || effective > max || configured > max || selected > max
    || (selected > 0 && selected !== effective)) {
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
      || min > max || effective < min || effective > max
      || configured < min || configured > max) {
      throw new Error('AI settings returned an unsupported time budget.');
    }
    budgetSettings = { value: effective, configured, min, max };
  }
  let tokenLimitSettings = null;
  if ('generation_token_limit' in budget || 'configured_generation_token_limit' in budget) {
    const min = budget.min_generation_token_limit;
    const max = budget.max_generation_token_limit;
    const effective = budget.generation_token_limit;
    const configured = budget.configured_generation_token_limit;
    if (min !== 0 || !validCount(max) || !validCount(effective, min) || !validCount(configured, min)
      || effective > max || configured > max) {
      throw new Error('AI settings returned an unsupported token limit.');
    }
    tokenLimitSettings = { value: effective, configured, min, max };
  }
  let schemeNodeLimitSettings = null;
  if ('scheme_max_nodes' in budget || 'configured_scheme_max_nodes' in budget) {
    const min = budget.min_scheme_max_nodes;
    const max = budget.max_scheme_max_nodes;
    const effective = budget.scheme_max_nodes;
    const configured = budget.configured_scheme_max_nodes;
    if (!validCount(min) || !validCount(max, min) || !validCount(effective, min) || !validCount(configured, min)
      || effective > max || configured > max) {
      throw new Error('AI settings returned an unsupported Scheme node limit.');
    }
    schemeNodeLimitSettings = { value: effective, configured, min, max };
  }
  return { ...settings, selected_extraction_workers: selected,
    profileSettings: parseGenerationProfileSettings(value), budgetSettings, tokenLimitSettings,
    schemeSourceSettings: parseSchemeSourceSettings(value), schemeNodeLimitSettings,
    advancedSchemeSettings: parseAdvancedSchemeSettings(value) } as WorkerSettings;
}

type SettingField = 'extraction_workers' | 'generation_profile' | 'generation_budget' | 'generation_token_limit'
  | 'scheme_source_chars' | 'scheme_max_nodes' | AdvancedSchemeField;
type SettingOption = string | number;
type Setting = { value: SettingOption; configured: SettingOption } &
  ({ min: number; max: number } | { options: readonly SettingOption[] });
type SettingEdit = { field: SettingField; draft: string; numericInput: boolean };
type SaveNotice = { state: 'saving' | 'saved' } | { state: 'failed'; error: string };
const savedMessages: Record<SettingField, string> = {
  extraction_workers: 'AI worker setting saved.',
  generation_profile: 'Generation profile saved.',
  generation_budget: 'Overall time budget saved.',
  generation_token_limit: 'Token limit saved.',
  scheme_source_chars: 'Scheme excerpt budget saved.',
  scheme_max_nodes: 'Scheme node limit saved.',
  scheme_read_bytes: 'Local file read limit saved.',
  scheme_package_chars: 'Final evidence package limit saved.',
};

function settingFor(settings: WorkerSettings | null, field: SettingField, numericInput = false): Setting | null | undefined {
  if (!settings) return null;
  switch (field) {
    case 'extraction_workers':
      return { value: settings.selected_extraction_workers, configured: settings.configured_extraction_workers,
        min: numericInput ? 1 : settings.min_extraction_workers, max: settings.max_extraction_workers };
    case 'generation_profile': {
      const profile = settings.profileSettings;
      return profile && { value: profile.generation_profile, configured: profile.configured_generation_profile,
        options: profile.generation_profiles.map(option => option.id) };
    }
    case 'generation_budget': return settings.budgetSettings;
    case 'generation_token_limit': return settings.tokenLimitSettings;
    case 'scheme_source_chars': return settings.schemeSourceSettings;
    case 'scheme_max_nodes': return settings.schemeNodeLimitSettings;
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
  const tokenLimitHelpId = useId();
  const schemeHelpId = useId();
  const schemeNodeHelpId = useId();
  const advancedSchemeHelpId = useId();
  const [settings, setSettings] = useState<WorkerSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [reload, setReload] = useState(0);
  const [drafts, setDrafts] = useState<Partial<Record<SettingField, SettingEdit>>>({});
  const [notices, setNotices] = useState<Partial<Record<SettingField, SaveNotice>>>({});
  const [autoWorkerStatus, setAutoWorkerStatus] = useState<AutoWorkerStatus | null>(null);
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
      const setting = settingFor(currentSettings.current, field, edit.numericInput);
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
    const edit = { field, draft, numericInput };
    latestEdits.current[field] = edit;
    setDrafts(previous => ({ ...previous, [field]: edit }));
    setNotices(previous => ({ ...previous, [field]: undefined }));
    if (settingValue(settingFor(currentSettings.current, field, numericInput), draft) === undefined) return;
    if (numericInput) {
      timers.current.set(field, setTimeout(() => finishNumber(field), 550));
    } else {
      enqueueSave(edit);
    }
  }

  function inputValue(field: SettingField) {
    const value = settingFor(settings, field)?.value;
    return drafts[field]?.draft ?? (value === undefined ? '' : String(value));
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
  const tokenLimit = settings?.tokenLimitSettings;
  const schemeBudget = settings?.schemeSourceSettings;
  const schemeNodeLimit = settings?.schemeNodeLimitSettings;
  const advancedScheme = settings?.advancedSchemeSettings;
  const workerInput = inputValue('extraction_workers');
  const autoWorkers = workerInput === '0' && !drafts.extraction_workers?.numericInput;
  const showAutoStatus = autoWorkers && settings?.selected_extraction_workers === 0;
  useEffect(() => {
    setAutoWorkerStatus(null);
    if (!active || !showAutoStatus) return;
    const controller = new AbortController();
    const { signal } = controller;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function refreshStatus() {
      try {
        const response = await fetch('/api/settings/ai/workers', { signal, cache: 'no-store' });
        const result = response.ok ? autoWorkerStatusResponse(await response.json()) : null;
        if (!signal.aborted) setAutoWorkerStatus(result);
        // Older servers still provide the CPU ceiling in the settings response.
        if (response.status === 404 || response.status === 405) return;
      } catch {
        if (!signal.aborted) setAutoWorkerStatus(null);
      }
      if (!signal.aborted) timer = setTimeout(() => { void refreshStatus(); }, 3000);
    }
    void refreshStatus();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [active, showAutoStatus, refreshVersion, reload]);
  const validWorkers = autoWorkers || settingValue(settingFor(settings, 'extraction_workers', true), workerInput) !== undefined;
  const workerOverride = settings && settings.selected_extraction_workers !== settings.configured_extraction_workers;
  const validBudget = settingValue(budget, inputValue('generation_budget')) !== undefined;
  const validTokenLimit = settingValue(tokenLimit, inputValue('generation_token_limit')) !== undefined;
  const validSchemeNodeLimit = settingValue(schemeNodeLimit, inputValue('scheme_max_nodes')) !== undefined;
  return <div>
    {loading && <Feedback>Loading AI settings…</Feedback>}
    <ErrorBox error={loadError} />
    {!!loadError && <button type="button" className="secondary" onClick={() => setReload(value => value + 1)}>
      Reload AI settings
    </button>}
    <label>
      Conversation review depth
      <select aria-label="Conversation review depth" value={inputValue('generation_profile')}
        disabled={loading || !profileSettings}
        onChange={event => changeSetting('generation_profile', event.target.value)}>
        {!profileSettings && <option value="">{loading ? 'Loading…' : 'Profiles unavailable'}</option>}
        {profileSettings?.generation_profiles.map((option) => <option key={option.id} value={option.id}>
          {generationProfileLabels[option.id]}{option.id === 'quality' ? ' (default)' : option.experimental ? ' (experimental)' : ''}
        </option>)}
      </select>
    </label>
    <SettingsHelp title="How review depth works">
      <ul>
        <li><strong>Thorough (default).</strong> Knowledge reads available original messages in smaller batches to preserve detail.</li>
        <li><strong>Balanced (experimental).</strong> Knowledge shares the source budget across conversations and skips imported tool logs.</li>
        <li><strong>Summary-assisted (experimental).</strong> Reuses conversation overviews and builds missing ones. Knowledge checks selected original messages.</li>
      </ul>
      <p>Building missing overviews can add time and tokens. Summary-assisted uses low reasoning when the model supports it and you leave effort unset.</p>
    </SettingsHelp>
    {saveFeedback('generation_profile')}
    {profileSettings && profileSettings.generation_profile !== profileSettings.configured_generation_profile
      && <p><small>Startup environment overrides the saved profile ({generationProfileLabels[profileSettings.configured_generation_profile]}).</small></p>}
    <label>
      AI worker mode
      <select aria-describedby={helpId} value={settings ? (autoWorkers ? 'auto' : 'fixed') : ''}
        disabled={loading || !settings}
        onChange={event => {
          if (!settings) return;
          const count = event.target.value === 'auto' ? 0
            : settings.configured_extraction_workers || settings.extraction_workers;
          changeSetting('extraction_workers', String(count));
        }}>
        {!settings && <option value="">{loading ? 'Loading…' : 'Unavailable'}</option>}
        <option value="auto" disabled={settings?.min_extraction_workers !== 0}>Auto</option>
        <option value="fixed">Fixed</option>
      </select>
    </label>
    {showAutoStatus && <p><small>{autoWorkerStatus
      ? <>Auto target {autoWorkerStatus.target} of {autoWorkerStatus.ceiling} workers
        {' '}· {autoWorkerStatus.active} active · {autoWorkerStatus.waiting} waiting</>
      : <>Up to {settings.extraction_workers} {settings.extraction_workers === 1 ? 'worker' : 'workers'} shared across Auto jobs.</>
    }</small></p>}
    {settings && !autoWorkers && <label>
      AI workers per job
      <input type="number" min={1} max={settings.max_extraction_workers} step={1}
        aria-describedby={helpId} aria-invalid={!validWorkers}
        value={workerInput} disabled={loading} placeholder="Workers per job"
        {...numericEvents('extraction_workers')} />
    </label>}
    {saveFeedback('extraction_workers')}
    {settings && !validWorkers && <Feedback>Enter a whole number from 1 to {settings.max_extraction_workers}.</Feedback>}
    <SettingsHelp title="Worker-count explanation" id={helpId}>
      <p>Auto shares a CPU-based ceiling across Auto jobs. It increases the target when batches keep waiting and
        {' '}requests succeed, and reduces it when the provider throttles requests or local memory is low.</p>
      <p>Fixed sets the maximum parallel batches for each job. Choose 1 to run batches serially.
        {' '}Changes apply to the next job.</p>
    </SettingsHelp>
    {workerOverride && <p><small>Startup environment overrides the saved value
      {' '}({settings.configured_extraction_workers || 'Auto'}).</small></p>}
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
    {tokenLimit && <>
      <label>
        Stop at reported tokens
        <input type="number" min={tokenLimit.min} max={tokenLimit.max} step={1}
          aria-describedby={tokenLimitHelpId} aria-invalid={!validTokenLimit}
          value={inputValue('generation_token_limit')} disabled={loading}
          {...numericEvents('generation_token_limit')} />
      </label>
      {saveFeedback('generation_token_limit')}
      {!validTokenLimit && <Feedback>Enter a whole number from 0 to {tokenLimit.max.toLocaleString()}.</Feedback>}
      <SettingsHelp title="About the reported token limit" id={tokenLimitHelpId}>
        <p>One limit covers all selected results, including parallel batches and retries. Set 0 for unlimited.
          {' '}Changes apply to the next generation.</p>
        <p>The total is approximate because provider reports can be incomplete or delayed.
          ProjectHub stops starting new requests when the reported total reaches the limit.
          Requests in progress can push the final total over the limit.</p>
      </SettingsHelp>
      {tokenLimit.value !== tokenLimit.configured && <p><small>Startup environment overrides the saved token limit
        {' '}({tokenLimit.configured === 0 ? 'unlimited' : tokenLimit.configured.toLocaleString()}).</small></p>}
    </>}
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
    {schemeNodeLimit && <>
      <label>
        Scheme node limit
        <input type="number" min={schemeNodeLimit.min} max={schemeNodeLimit.max} step={1}
          aria-describedby={schemeNodeHelpId} aria-invalid={!validSchemeNodeLimit}
          value={inputValue('scheme_max_nodes')} disabled={loading}
          {...numericEvents('scheme_max_nodes')} />
      </label>
      {saveFeedback('scheme_max_nodes')}
      {!validSchemeNodeLimit && <Feedback>Enter a whole number from {schemeNodeLimit.min} to {schemeNodeLimit.max}.</Feedback>}
      <SettingsHelp title="About the Scheme node limit" id={schemeNodeHelpId}>
        <p>Caps the number of nodes in each generated diagram. Architecture, Workflow, and Focused Scheme each
          get their own limit. The default is 20. Changes apply to the next generation.</p>
      </SettingsHelp>
      {schemeNodeLimit.value !== schemeNodeLimit.configured
        && <p><small>Startup environment overrides the saved node limit ({schemeNodeLimit.configured}).</small></p>}
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
