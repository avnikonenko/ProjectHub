import { useGenerationRequest, type GenerationRequestProps } from './generationBatch';
import { useEffect, useId, useRef, useState } from 'react';
import { api } from './api';
import { AIResultHeader } from './AIResultHeader';
import { AIEmphasis } from './AIEmphasis';
import { BriefSources, briefReferences } from './BriefReferences';
import { CoverageDetails } from './CoverageDetails';
import { Feedback } from './Feedback';
import { GenerationStatus, SavedGeneration, isGenerationCancelled, cancelledGenerationMessage } from './GenerationStatus';
import { RevisionHistory } from './RevisionHistory';
import { Empty, ErrorBox } from './ui';
import { filterParameters, parameterDefault, parameterGroups, parseParameterResult, parseParameterSnapshot,
  parameterRevisionResult, isImplementationChoice,
  type ParameterResult, type ParameterSnapshot, type ProjectParameter } from './parameters';
import './knowledge.css';

type Generation = { model: string; effort: string };
type Request = (path: string, options: RequestInit) => Promise<unknown>;
type Busy = { busy: boolean; running: boolean };
type Progress = { state: 'idle' | 'running' | 'completed' | 'failed' | 'cancelled'; started_at?: string; error?: string };
const bases = { documented: 'Documented in inspected evidence', inferred: 'AI-inferred', unknown: 'Not established' };

export function parameterFreshnessLabel(freshness: ParameterResult['freshness'] | undefined, historical = false) {
  if (historical) return undefined;
  if (freshness?.state === 'changed') return 'Inspected sources changed. Refresh implementation choices to update this result.';
  if (freshness?.state === 'unchanged') return 'No changes detected in inspected sources';
  if (freshness?.state === 'unknown') return 'Source freshness is unknown.';
  return undefined;
}

export function ParameterCoverage({ snapshot, freshness, historical = false }: {
  snapshot: ParameterSnapshot; freshness?: ParameterResult['freshness']; historical?: boolean;
}) {
  return <CoverageDetails savedAt={snapshot.generated_at} sourceCount={snapshot.sources.length}
    coverage={snapshot.coverage} diagnostics={snapshot.diagnostics}>
    {!historical && freshness && (freshness.state === 'unknown' || !!freshness.limitations.length) &&
      <details className="parameter-current-source-check"><summary>Current source check</summary>
        <p>This read-only check describes sources now, separately from the inspection recorded with the saved result.</p>
        {freshness.state === 'unknown' && <p>Source freshness could not be established. Saved declarations remain available.</p>}
        {!!freshness.limitations.length && <ul>{freshness.limitations.map((value, index) => <li key={index}>{value}</li>)}</ul>}
      </details>}
  </CoverageDetails>;
}

export async function generateProjectParameters(base: string, generation: Generation,
  signal: AbortSignal, request: Request = api): Promise<ParameterSnapshot> {
  if (!/^\/projects\/[1-9]\d*$/.test(base)) throw new Error('The parameters request does not identify a project.');
  signal.throwIfAborted();
  const raw = await request(`${base}/codex/parameters`, { method: 'POST', signal,
    body: JSON.stringify({ confirmed: true, model: generation.model || null, effort: generation.effort || null }) });
  signal.throwIfAborted();
  return parseParameterSnapshot(raw, Number(base.split('/')[2]));
}

function pause(signal: AbortSignal) {
  return new Promise<void>(resolve => {
    const done = () => { clearTimeout(timer); signal.removeEventListener('abort', done); resolve(); };
    const timer = setTimeout(done, 1500); signal.addEventListener('abort', done, { once: true });
    if (signal.aborted) done();
  });
}
function parseProgress(raw: unknown): Progress {
  if (!raw || typeof raw !== 'object' || !('state' in raw)
      || !['idle', 'running', 'completed', 'failed', 'cancelled'].includes(String(raw.state)))
    throw new Error('The parameters generation status is invalid.');
  const status = raw as Progress;
  if ((status.started_at !== undefined && typeof status.started_at !== 'string')
      || (status.error !== undefined && status.error !== null && typeof status.error !== 'string'))
    throw new Error('The parameters generation status is invalid.');
  return status;
}

/** Status/saved-result reads only: recovering a lost response never resends generation. */
export async function recoverProjectParameters(base: string, projectId: number, signal: AbortSignal,
  notify: (status: Progress, result?: ParameterResult) => void, request: Request = api,
  wait: (signal: AbortSignal) => Promise<void> = pause, handoff?: { previousStartedAt?: string }) {
  if (!Number.isSafeInteger(projectId) || projectId <= 0 || base !== `/projects/${projectId}`)
    throw new Error('The parameters recovery does not identify this project.');
  signal.throwIfAborted();
  const statusPath = `${base}/codex/generation/status?kind=parameters`;
  const [rawResult, rawStatus] = await Promise.all([
    request(`${base}/codex/parameters`, { signal }), request(statusPath, { signal }),
  ]);
  let status = parseProgress(rawStatus);
  const initial = parseParameterResult(rawResult, projectId);
  if (signal.aborted) return;
  for (let attempt = 0; handoff && attempt < 2 && status.state !== 'running'
       && status.started_at === handoff.previousStartedAt; attempt++) {
    await wait(signal); if (signal.aborted) return;
    status = parseProgress(await request(statusPath, { signal }));
  }
  if (signal.aborted) return;
  notify(status, initial);
  let wasRunning = false;
  while (status.state === 'running' && !signal.aborted) {
    wasRunning = true;
    await wait(signal); if (signal.aborted) return;
    status = parseProgress(await request(statusPath, { signal }));
    if (!signal.aborted) notify(status);
  }
  if (signal.aborted) return;
  if (wasRunning || status.state === 'completed' || handoff) {
    const result = parseParameterResult(await request(`${base}/codex/parameters`, { signal }), projectId);
    if (!signal.aborted) notify(status, result);
  }
}

function SavedCodeLinks({ ids, snapshot, prefix }: {
  ids: string[]; snapshot: ParameterSnapshot; prefix: string;
}) {
  return <ul>{[...new Set(ids)].flatMap(id => {
    const index = snapshot.sources.findIndex(source => source.id === id);
    if (index < 0) return [];
    const source = snapshot.sources[index];
    const target = `${prefix}-source-${index + 1}`;
    return <li key={id}><a href={`#${target}`} onClick={event => {
      event.preventDefault();
      const evidence = document.getElementById(target);
      for (let element = evidence; element; element = element.parentElement)
        if (element instanceof HTMLDetailsElement) element.open = true;
      evidence?.scrollIntoView({ block: 'nearest' });
    }}>{source.path ? `${source.path}${source.line_start ? ':' + source.line_start : ''}${source.line_end && source.line_end !== source.line_start ? '–' + source.line_end : ''}` : source.label}</a>
      {source.symbol && <small> · {source.symbol}</small>}</li>;
  })}</ul>;
}

export function ParameterList({ snapshot, prefix, query = '', component = '' }: {
  snapshot: ParameterSnapshot; prefix: string; query?: string; component?: string;
}) {
  const numbers = new Map(snapshot.sources.map((source, index) => [source.id, index + 1]));
  const filtered = filterParameters(snapshot.items, query, component, snapshot.sources);
  const findings = filtered.filter(isImplementationChoice);
  const other = filtered.filter(item => !isImplementationChoice(item));
  const explanation = (item: ProjectParameter, field: 'purpose' | 'rationale' | 'trade_off' | 'behavior', title: string) => {
    const value = item[field];
    const basis = field === 'purpose' ? item.purpose_basis : field === 'rationale' ? item.rationale_basis
      : field === 'behavior' ? item.behavior_basis ?? 'unknown' : item.trade_off_basis;
    return <><dt>{title}</dt><dd>{value ? <><AIEmphasis text={value} /> <small>({bases[basis]})</small>
      {briefReferences(item.explanation_source_ids, numbers, prefix, snapshot.sources)}</>
      : 'Not documented in the inspected evidence.'}</dd></>;
  };
  const records = (selected: ProjectParameter[]) => parameterGroups(selected).map(([name, items]) => <section className="knowledge-group" key={name}>
    <h3>{name || 'Unspecified component'} <small>({items.length})</small></h3>
    {items.map(item => {
      const choice = isImplementationChoice(item);
      const inline = item.declaration_kind === 'implementation_behavior';
      return <details className="knowledge-item" key={item.id}>
        <summary><span className="knowledge-item-title">{choice ? item.title || item.name : item.name}</span>
          <small className="knowledge-item-meta"><span>{inline ? 'Code behavior' : <>{item.declaration_kind === 'configuration_value' ? 'Declared value' : 'Default'}: <code>{parameterDefault(item)}</code></>}
            {item.default_kind === 'expression' && ' · expression; not evaluated'}
            {item.condition && ' · conditional declaration'}</span>
            <span>{item.path}:{item.line_start}</span></small>
        </summary>
        <div className="knowledge-item-body">
          <dl className="knowledge-fields">
            {choice && explanation(item, 'behavior', 'Implementation behavior')}
            {explanation(item, 'trade_off', 'Project-specific consequence')}
            <dt>{inline ? 'Code location' : 'Defined at'}</dt><dd><SavedCodeLinks ids={item.source_ids} snapshot={snapshot} prefix={prefix} /></dd>
            <dt>Applied / referenced at</dt><dd>{item.usage_source_ids?.length
              ? <SavedCodeLinks ids={item.usage_source_ids} snapshot={snapshot} prefix={prefix} />
              : 'No usage site established by this saved inspection. A declaration alone does not prove enforcement.'}</dd>
            {explanation(item, 'rationale', 'Documented reason')}
          </dl>
          <details><summary>Implementation details & defaults</summary>
            <p className="muted">{inline ? 'Inspected implementation behavior' : 'Code/configuration declaration'}</p>
            <dl className="knowledge-fields">
              {!inline && <><dt>{item.declaration_kind === 'configuration_value' ? 'Declared configuration value' : 'Declared default'}</dt><dd><code>{parameterDefault(item)}</code>
                {briefReferences(item.source_ids, numbers, prefix, snapshot.sources)}</dd></>}
              {item.default_kind === 'expression' && <><dt>Default form</dt><dd>Expression preserved from code; its runtime value was not evaluated.</dd></>}
              {!!item.condition && <><dt>Declaration condition</dt><dd><code>{item.condition}</code><small> — not evaluated</small></dd></>}
              <dt>Location</dt><dd><code>{item.path}:{item.line_start}{item.line_end !== item.line_start && `–${item.line_end}`}</code></dd>
              {!!item.symbol && <><dt>Symbol</dt><dd><code>{item.symbol}</code></dd></>}
              {explanation(item, 'purpose', 'Purpose')}
              {item.units && <><dt>Units noted in explanation</dt><dd>{item.units}</dd></>}
            </dl>
          </details>
          <details><summary>Saved code evidence</summary>
            {[...new Set([...item.source_ids, ...(item.usage_source_ids ?? [])])].map(id => {
              const source = snapshot.sources.find(value => value.id === id)!;
              return <article key={id}><strong>{source.label}</strong>
                {source.revision && <p><small>{source.revision_kind === 'inspected_prefix_hash' ? 'Inspected file prefix hash' : 'Inspected file content hash'}: {source.revision}</small></p>}
                <pre className="json-report">{source.text}</pre>
              </article>;
            })}
          </details>
        </div>
      </details>;
    })}
  </section>);
  return <>
    <p role="status">{filtered.length} of {snapshot.items.length} inspected records · {findings.length} implementation choices</p>
    {snapshot.view_version !== 2 && <p className="muted">This saved version contains parameter declarations. Refresh to discover implementation choices and their usage sites; existing records remain available below.</p>}
    {!filtered.length && <Empty>{snapshot.items.length ? 'No records match these filters.' : 'No supported code choices or parameter declarations were found in this saved inspection.'}</Empty>}
    {!!filtered.length && !findings.length && <p>No implementation choices with supporting usage evidence were established in these results.</p>}
    {records(findings)}
    {!!other.length && <details className="parameter-other-defaults"><summary>Other inspected defaults ({other.length})</summary>
      <p>These declarations remain searchable. Their existence does not establish that they are enforced or represent a consequential implementation choice.</p>
      {records(other)}
    </details>}
  </>;
}

export function ProjectParameters({ base, generation, visible, externalBusy = false, onBusyChange, generationRequest = 0, onGenerationRequestHandled }: GenerationRequestProps & {
  base: string; generation: Generation; visible: boolean; externalBusy?: boolean;
  onBusyChange?: (state: Busy) => void;
}) {
  const projectId = Number(base.split('/')[2]);
  const [saved, setSaved] = useState<{ base: string; result: ParameterResult | null }>({ base, result: null });
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [retry, setRetry] = useState(0);
  const [revisionId, setRevisionId] = useState<number | null>(null);
  const [revisionLoading, setRevisionLoading] = useState(false);
  const [latestVersion, setLatestVersion] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [component, setComponent] = useState('');
  const requestVersion = useRef(0);
  const active = useRef<{ base: string; controller: AbortController } | null>(null);
  const reader = useRef<AbortController | null>(null);
  const selectedRevision = useRef<number | null>(null);
  const selectingRevision = useRef(false);
  const lastStartedAt = useRef<string | undefined>(undefined);
  const handoff = useRef<{ previousStartedAt?: string } | undefined>(undefined);
  const notifying = useRef(onBusyChange); notifying.current = onBusyChange;
  const result = saved.base === base ? saved.result : null;
  const snapshot = result?.snapshot;
  const busy = loading || generating || recovering || revisionLoading;
  const instance = useId();
  const prefix = `${projectId}-parameters-${instance}`;

  useEffect(() => { notifying.current?.({ busy, running: generating || recovering }); }, [busy, generating, recovering]);
  useEffect(() => { setQuery(''); setComponent(''); }, [base, snapshot?.generated_at]);
  useEffect(() => {
    setSaved({ base, result: null }); setLoading(true); setGenerating(false); setRecovering(false);
    setError(''); setNotice(''); setRevisionId(null); setLatestVersion(null); setQuery(''); setComponent('');
    selectedRevision.current = null; selectingRevision.current = false;
    lastStartedAt.current = undefined; handoff.current = undefined;
    return () => {
      requestVersion.current++; reader.current?.abort(); active.current?.controller.abort();
      reader.current = null; active.current = null; notifying.current?.({ busy: false, running: false });
    };
  }, [base]);
  useEffect(() => {
    if ((!visible && !generationRequest && !recovering && result !== null) || active.current || selectingRevision.current) return;
    const controller = new AbortController(); reader.current?.abort(); reader.current = controller;
    const identity = ++requestVersion.current;
    const valid = () => !controller.signal.aborted && requestVersion.current === identity;
    const attempt = handoff.current; handoff.current = undefined;
    if (!result) setLoading(true);
    recoverProjectParameters(base, projectId, controller.signal, (status, current) => {
      if (!valid()) return;
      lastStartedAt.current = status.started_at;
      setRecovering(status.state === 'running');
      if (status.state === 'failed') setError(status.error || 'Parameter generation failed. The previous saved result is unchanged.');
      else if (status.state === 'cancelled') setNotice(cancelledGenerationMessage);
      if (current) {
        setLoading(false);
        if (status.state !== 'failed' && (!attempt || status.state === 'completed')) setError('');
        setLatestVersion(current.snapshot?.generated_at ?? null);
        setSaved(previous => ({ base, result: selectedRevision.current !== null && previous.base === base && previous.result
          ? { ...current, snapshot: previous.result.snapshot } : current }));
      }
    }, api, pause, attempt).catch(caught => {
      if (valid()) { setError((caught as Error).message); setRecovering(false); }
    }).finally(() => { if (valid()) setLoading(false); });
    return () => { controller.abort(); if (reader.current === controller) reader.current = null; };
  }, [base, projectId, retry, visible, generationRequest]);

  async function generate() {
    if (busy || externalBusy || active.current) return;
    const run = { base, controller: new AbortController() };
    reader.current?.abort(); requestVersion.current++;
    active.current = run; setGenerating(true); setError(''); setNotice('');
    try {
      const current = await generateProjectParameters(base, generation, run.controller.signal);
      if (active.current !== run || run.controller.signal.aborted) return;
      selectedRevision.current = null; setRevisionId(null); setLatestVersion(current.generated_at);
      setSaved({ base, result: { snapshot: current,
        freshness: { state: 'unknown', partial: current.coverage.partial === true,
          limitations: Array.isArray(current.coverage.limitations) ? current.coverage.limitations.filter((value): value is string => typeof value === 'string') : [] } } });
      setRetry(value => value + 1);
    } catch (caught) {
      if (active.current !== run || run.controller.signal.aborted) return;
      if (isGenerationCancelled(caught)) setNotice(cancelledGenerationMessage);
      else {
        setError((caught as Error).message); setRecovering(true);
        handoff.current = { previousStartedAt: lastStartedAt.current };
        setRetry(value => value + 1);
      }
    } finally {
      if (active.current === run) { active.current = null; setGenerating(false); }
    }
  }
  useGenerationRequest(base, generationRequest, generating || recovering || revisionLoading || externalBusy ? 'skip' : loading ? 'wait' : 'ready', generate, onGenerationRequestHandled);
  const choices = [...new Set(snapshot?.items.map(item => item.component).filter(Boolean) ?? [])].sort();
  const freshness = parameterFreshnessLabel(result?.freshness, revisionId !== null);
  return <section className="card project-parameters" aria-busy={busy}>
    <AIResultHeader title="Implementation Choices & Limits" savedAt={snapshot?.generated_at} previous={revisionId !== null}
      freshness={freshness} action={<button disabled={busy || externalBusy} onClick={generate}>
        {generating ? 'Generating…' : snapshot ? 'Refresh implementation choices' : 'Generate implementation choices'}
      </button>} />
    <p>Consequential limits, filters, fallbacks and processing choices found in code, with their consequences and usage locations. These are not values verified in a particular run.</p>
    <details><summary>How this works</summary>
      <p>Explains implementation choices and limits, their practical consequences, and where they are defined and used in the code. Generation sends selected code to OpenAI through Codex.</p>
    </details>
    <ErrorBox error={error} /><Feedback>{notice}</Feedback>
    {loading && <Feedback>Loading saved implementation choices…</Feedback>}
    {(generating || recovering) && <GenerationStatus base={base} kind="parameters" label="Implementation Choices & Limits" />}
    {!loading && !snapshot && <Empty>No saved implementation choices yet. Register a repository, then generate this view when ready.</Empty>}
    {snapshot && <>
      <div className="knowledge-filters">
        <label>Search implementation choices<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Limit, behavior, consequence, location or default…" /></label>
        <label>Component<select value={component} onChange={event => setComponent(event.target.value)}>
          <option value="">All components</option>{choices.map(value => <option key={value} value={value}>{value}</option>)}
        </select></label>
        <button className="secondary" onClick={() => { setQuery(''); setComponent(''); }}>Reset filters</button>
      </div>
      <ParameterList snapshot={snapshot} prefix={prefix} query={query} component={component} />
      <ParameterCoverage snapshot={snapshot} freshness={result?.freshness} historical={revisionId !== null} />
      <details><summary>Generation & saved versions</summary>
        <SavedGeneration generation={snapshot.generation} />
        <RevisionHistory<ParameterSnapshot> base={base} kind="parameters" version={latestVersion}
          disabled={busy || externalBusy} selectedId={revisionId} onLoadingChange={value => {
            selectingRevision.current = value; if (value) { requestVersion.current++; reader.current?.abort(); setLoading(false); }
            setRevisionLoading(value);
          }} onSelect={(current, id) => {
            const parsed = current === null ? null : parseParameterSnapshot(current, projectId);
            requestVersion.current++; selectedRevision.current = id; setRevisionId(id); setQuery(''); setComponent('');
            if (id === null) { setLatestVersion(parsed?.generated_at ?? null); setRetry(value => value + 1); }
            setSaved(previous => ({ base, result: parameterRevisionResult(
              previous.base === base ? previous.result : null, parsed, id !== null) }));
          }} />
      </details>
      <details><summary>Code & configuration sources ({snapshot.sources.length})</summary>
        <BriefSources sources={snapshot.sources} prefix={prefix} />
      </details>
    </>}
  </section>;
}
