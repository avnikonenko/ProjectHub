import { schemeFocusLabels, useGenerationRequest, type GenerationRequestProps, type SchemeFocus } from './generationBatch';
import { AIEmphasis } from './AIEmphasis';
import { Feedback } from './Feedback';
import { useEffect, useId, useRef, useState } from 'react';
import { api, post } from './api';
import { briefReferences, BriefSources, type BriefSource } from './BriefReferences';
import {
  GenerationStatus, SavedGeneration, useFocusedSchemeRecovery, isGenerationCancelled,
  cancelledGenerationMessage, type GenerationProvenance,
} from './GenerationStatus';
import { Empty, ErrorBox, label } from './ui';
import { RevisionHistory } from './RevisionHistory';
import { decodeSchemeText } from './SchemeText';
import { schemeNodeRoles } from './schemeRoles';
import { AIResultHeader } from './AIResultHeader';
import { SchemeResultCoverage, schemeInspectionForDisplay, type SchemeCoverage, type SchemeDiagnostics } from './SchemeInspectionDetails';
import { SchemeKnowledgeContext } from './SchemeKnowledgeContext';
import { SchemeDependencies, type Dependency, type DependencyInventory } from './SchemeDependencies';
import { generationBody, generationDestination, type Generation } from './generationProvider';

type SchemeSource = BriefSource & {
  provenance: string; content_hash: string; path?: string; revision?: string; date?: string;
};
type SchemeNode = {
  id: string; label: string; description: string; type: string;
  status: 'observed' | 'proposed' | 'uncertain'; source_ids: string[]; paths: string[];
};
type SchemeEdge = {
  source: string; target: string; label: string; type: string;
  status: SchemeNode['status']; source_ids: string[];
};
type SchemeGraph = { nodes: SchemeNode[]; edges: SchemeEdge[]; explanation: string; mermaid: string };
type TreeEntry = { path: string; type: 'file' | 'directory' };
type Coverage = SchemeCoverage;
type SchemeSnapshot = {
  generated_at: string; generation?: GenerationProvenance | null;
  focus?: SchemeFocus;
  architecture: SchemeGraph; workflow: SchemeGraph; dependencies: Dependency[];
  sources: SchemeSource[]; tree: TreeEntry[]; coverage: Coverage; fingerprint: string; diagnostics?: SchemeDiagnostics | null;
  knowledge_context?: unknown;
  dependency_inventory?: DependencyInventory;
};
type SchemeResult = {
  snapshot: SchemeSnapshot | null;
  freshness: { state: string; partial: boolean; limitations: string[] };
  inspection: { tree: TreeEntry[]; dependencies: Dependency[]; coverage: Coverage; sources?: SchemeSource[];
    diagnostics?: SchemeDiagnostics | null; dependency_inventory?: DependencyInventory };
};
type FocusedSchemeSnapshot = {
  generated_at: string; instruction: string; graph: SchemeGraph; sources: SchemeSource[];
  coverage: Coverage; generation?: GenerationProvenance | null; diagnostics?: SchemeDiagnostics | null;
};
const schemeViews = ['Architecture', 'Workflow', 'Dependencies', 'Focused Scheme'] as const;

const freshnessLabels: Record<string, string> = {
  unchanged: 'No changes detected in inspected sources',
  current: 'No changes detected in inspected sources',
  fresh: 'No changes detected in inspected sources',
  changed: 'Source changes detected',
  stale: 'Source changes detected',
  unknown: 'Freshness unknown',
  not_generated: 'Not generated',
};

function SchemeDiagram({ graph }: { graph: SchemeGraph }) {
  const container = useRef<HTMLDivElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const viewer = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const [fullscreen, setFullscreen] = useState(false);
  const [viewError, setViewError] = useState('');
  const id = useId().replace(/[^a-zA-Z0-9]/g, '');
  const [error, setError] = useState('');
  const [rendering, setRendering] = useState(false);
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme);

  function fitDiagram() {
    const svg = container.current?.querySelector('svg');
    const width = viewport.current?.clientWidth;
    const bounds = svg?.getAttribute('viewBox')?.trim().split(/[ ,]+/).map(Number);
    if (!width || !bounds || bounds.length !== 4 || !bounds.every(Number.isFinite)
        || bounds[2] <= 0 || bounds[3] <= 0) {
      setZoom(1);
      return;
    }
    const availableHeight = Math.max(160, Math.min(window.innerHeight * 0.65, 560));
    setZoom(Math.min(1, availableHeight * bounds[2] / (width * bounds[3])));
    viewport.current?.scrollTo({ top: 0, left: 0 });
  }

  async function toggleFullscreen() {
    setViewError('');
    try {
      if (document.fullscreenElement === viewer.current) {
        await document.exitFullscreen();
      } else if (viewer.current?.requestFullscreen) {
        await viewer.current.requestFullscreen();
      } else {
        setViewError('Fullscreen is unavailable in this browser. Zoom controls are still available.');
      }
    } catch {
      setViewError('Could not change fullscreen mode. Zoom controls are still available.');
    }
  }

  useEffect(() => {
    function changed() {
      setFullscreen(document.fullscreenElement === viewer.current);
      requestAnimationFrame(fitDiagram);
    }
    document.addEventListener('fullscreenchange', changed);
    return () => document.removeEventListener('fullscreenchange', changed);
  }, []);
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(document.documentElement.dataset.theme));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let active = true;
    const element = container.current;
    if (element) element.replaceChildren();
    setError('');
    setViewError('');
    setZoom(1);
    if (!graph.nodes.length || !graph.mermaid.trim()) { setRendering(false); return; }
    setRendering(true);
    void import('mermaid').then(async ({ default: mermaid }) => {
      // Generated diagrams have no HTML labels or navigation. Strict mode also
      // disables Mermaid callbacks and treats labels as untrusted text.
      mermaid.initialize({ theme: theme === 'dark' ? 'dark' : 'default', startOnLoad: false, htmlLabels: false, securityLevel: 'strict', suppressErrorRendering: true,
        flowchart: { htmlLabels: false, useMaxWidth: true } });
      const result = await mermaid.render('scheme' + id, graph.mermaid);
      if (!active || !element) return;
      const svg = new DOMParser().parseFromString(result.svg, 'image/svg+xml').documentElement;
      if (svg.localName !== 'svg') throw new Error('Invalid diagram output.');
      svg.querySelectorAll('script, foreignObject, image, a, iframe, object, embed').forEach((node) => node.remove());
      for (const node of [svg, ...svg.querySelectorAll('*')]) {
        for (const attribute of [...node.attributes]) {
          if (/^on/i.test(attribute.name) || /href$/i.test(attribute.name)) node.removeAttribute(attribute.name);
        }
      }
      // Decode only SVG label text after removing active content. Setting a text
      // node value cannot create markup, even when the label contains < or >.
      const textNodes = svg.ownerDocument.createTreeWalker(svg, NodeFilter.SHOW_TEXT);
      let textNode: Node | null;
      while ((textNode = textNodes.nextNode())) {
        if (textNode.parentElement?.closest('text, title, desc')) {
          textNode.nodeValue = decodeSchemeText(textNode.nodeValue ?? '');
        }
      }
      const roles = schemeNodeRoles(graph.nodes, graph.edges);
      const palette = theme === 'dark'
        ? { start: ['#254d43', '#76cbb1'], end: ['#49365d', '#c4a1e5'], step: ['#263d54', '#8bb7da'] }
        : { start: ['#def3e9', '#357a62'], end: ['#eee3f7', '#80549f'], step: ['#e3eef8', '#497ba5'] };
      for (const node of graph.nodes) {
        // Match the stable IDs generated by the backend, never label text.
        const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(node.id));
        const stableId = 'n' + [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('').slice(0, 16);
        const rendered = [...svg.querySelectorAll<SVGGElement>('g.node')]
          .find(element => element.id.includes(`-flowchart-${stableId}-`) || element.id.startsWith(`flowchart-${stableId}-`));
        if (!rendered) continue;
        const role = roles.get(node.id) ?? 'step';
        const [fill, stroke] = palette[role];
        rendered.querySelectorAll<SVGElement>(':scope > rect, :scope > polygon, :scope > circle, :scope > ellipse, :scope > path').forEach(shape => {
          shape.style.setProperty('fill', fill, 'important');
          shape.style.setProperty('stroke', stroke, 'important');
        });
        rendered.querySelectorAll<SVGElement>('text, tspan').forEach(text => {
          text.style.setProperty('fill', theme === 'dark' ? '#f3f6fa' : '#203345', 'important');
        });
      }
      if (!active) return;
      svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', 'Project scheme diagram');
      // Keep zoom outside Mermaid rendering and allow Fit on narrow screens.
      svg.style.width = '100%';
      svg.style.minWidth = '0';
      svg.style.maxWidth = 'none';
      svg.style.height = 'auto';
      element.replaceChildren(document.importNode(svg, true));
      fitDiagram();
    }).catch(() => { if (active) setError('The diagram could not be rendered. Try refreshing the page. Its components and evidence are available below.'); })
      .finally(() => { if (active) setRendering(false); });
    return () => { active = false; };
  }, [graph.mermaid, graph.nodes, graph.edges, id, theme]);
  const available = !rendering && !error && graph.nodes.length > 0 && !!graph.mermaid.trim();
  const supportsFullscreen = typeof document.documentElement.requestFullscreen === 'function'
    && document.fullscreenEnabled !== false;
  return <div ref={viewer} className="scheme-viewer">
    <div className="scheme-diagram-controls" role="group" aria-label="Diagram view controls">
      <button type="button" className="secondary" disabled={!available || zoom <= 0.25}
        aria-label="Zoom out diagram" onClick={() => setZoom((value) => Math.max(0.25, value - 0.25))}>−</button>
      <output aria-label="Diagram zoom">{Math.round(zoom * 100)}%</output>
      <button type="button" className="secondary" disabled={!available || zoom >= 3}
        aria-label="Zoom in diagram" onClick={() => setZoom((value) => Math.min(3, value + 0.25))}>+</button>
      <button type="button" className="secondary" disabled={!available} onClick={fitDiagram}>Fit diagram</button>
      <button type="button" className="secondary" disabled={!supportsFullscreen || !available}
        title={supportsFullscreen ? undefined : 'Fullscreen is unavailable in this browser'}
        aria-pressed={fullscreen} onClick={() => void toggleFullscreen()}>
        {fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
      </button>
    </div>
    <p className="scheme-color-key"><span className="scheme-key-start">Start / entry</span><span className="scheme-key-step">Intermediate</span><span className="scheme-key-end">End / output</span></p>
    {rendering && <Feedback>Rendering diagram…</Feedback>}
    <ErrorBox error={error || viewError} />
    <div ref={viewport} className="scheme-viewport" tabIndex={0}
      role="region" aria-label="Scrollable project diagram">
      <div ref={container} className="scheme-diagram" style={{ width: `${zoom * 100}%` }} />
    </div>
  </div>;
}

type Directory = { name: string; path: string; directory: boolean; children: Map<string, Directory> };
function CodeStructure({ entries, highlighted }: { entries: TreeEntry[]; highlighted: Set<string> }) {
  const root: Directory = { name: '', path: '', directory: true, children: new Map() };
  for (const entry of entries) {
    const parts = entry.path.split('/').filter(Boolean);
    let current = root;
    for (const [index, name] of parts.entries()) {
      const path = parts.slice(0, index + 1).join('/');
      let child = current.children.get(name);
      if (!child) {
        child = { name, path, directory: index < parts.length - 1 || entry.type === 'directory', children: new Map() };
        current.children.set(name, child);
      }
      current = child;
    }
  }
  function branch(directory: Directory) {
    return <ul>{[...directory.children.values()].sort((a, b) =>
      Number(b.directory) - Number(a.directory) || a.name.localeCompare(b.name)).map((entry) => (
      <li key={entry.path}>
        {entry.directory
          ? <details><summary>{entry.name}/</summary>{branch(entry)}</details>
          : <span className={highlighted.has(entry.path) ? 'scheme-referenced-file' : ''}
            title={highlighted.has(entry.path) ? 'Referenced by a diagram component' : entry.path}>{entry.name}</span>}
      </li>
    ))}</ul>;
  }
  return <details className="scheme-code-tree">
    <summary>Code structure</summary>
    {entries.length ? branch(root) : <p>No repository tree available for this result.</p>}
  </details>;
}

function GraphEvidence({ graph, numbers, prefix, sources }: { graph: SchemeGraph; numbers: Map<string, number>; prefix: string; sources: BriefSource[] }) {
  const nodes = new Map(graph.nodes.map((node) => [node.id, node.label]));
  return <details className="scheme-components">
    <summary>Components and relationships ({graph.nodes.length})</summary>
    <ul>{graph.nodes.map((node) => <li key={node.id}>
      <strong>{node.label}</strong> <span className="badge">{label(node.status)}</span>
      <p><AIEmphasis text={node.description} />{briefReferences(node.source_ids, numbers, prefix, sources)}</p>
      {!!node.paths.length && <small>{node.paths.join(' · ')}</small>}
    </li>)}</ul>
    {!!graph.edges.length && <details><summary>Relationships ({graph.edges.length})</summary><ul>
      {graph.edges.map((edge, index) => <li key={index}>
        {nodes.get(edge.source)} → {nodes.get(edge.target)}: {edge.label || label(edge.type)}
        {' '}<span className="badge">{label(edge.status)}</span>
        {briefReferences(edge.source_ids, numbers, prefix, sources)}
      </li>)}
    </ul></details>}
  </details>;
}

function FocusedScheme({ base, generation, visible, onGenerating }: {
  base: string; generation: Generation; visible: boolean; onGenerating?: (active: boolean) => void;
}) {
  const [snapshot, setSnapshot] = useState<FocusedSchemeSnapshot | null>(null);
  const [instruction, setInstruction] = useState('');
  const [loading, setLoading] = useState(true);
  const [retryLoad, setRetryLoad] = useState(0);
  const [generating, setGenerating] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [submissionError, setSubmissionError] = useState<{
    message: string; instruction: string; savedAt?: string; previousStartedAt?: string;
  } | null>(null);
  const [notice, setNotice] = useState('');
  const draftEdited = useRef(false);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const recovery = useFocusedSchemeRecovery(base, visible);
  const active = generating || recovery.running;
  const previousAttempt = useRef<string | undefined>(undefined);
  const status = recovery.progress;
  const recoveredCancellation = !active && !recovery.checking && status?.state === 'cancelled'
    && status.started_at !== previousAttempt.current;
  const recoveredSubmission = submissionError && !recovery.checking && status?.started_at
    && status.started_at !== submissionError.previousStartedAt
    && (status.state === 'cancelled' || (status.state === 'completed'
      && snapshot?.instruction === submissionError.instruction && snapshot.generated_at !== submissionError.savedAt));
  const path = base + '/codex/scheme/focused';
  const prefix = base.split('/')[2] + '-focused-scheme';
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { onGenerating?.(active); }, [active, onGenerating]);
  useEffect(() => { if (recoveredSubmission) setSubmissionError(null); }, [recoveredSubmission]);
  useEffect(() => {
    if (!visible || inFlight.current) return;
    const abort = new AbortController();
    setLoading(true);
    setLoadError('');
    api<{ snapshot: FocusedSchemeSnapshot | null }>(path, { signal: abort.signal })
      .then(({ snapshot: saved }) => {
        if (abort.signal.aborted) return;
        setSnapshot(saved);
        if (!draftEdited.current) setInstruction(saved?.instruction ?? '');
      })
      .catch((error: Error) => { if (!abort.signal.aborted) setLoadError(error.message); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [path, recovery.version, retryLoad, visible]);

  async function generate() {
    const requested = instruction.trim();
    if (!requested || loading || loadError || recovery.checking || active || inFlight.current) return;
    inFlight.current = true;
    previousAttempt.current = status?.started_at;
    setGenerating(true);
    recovery.clearError();
    setSubmissionError(null);
    setNotice('');
    try {
      const saved = await post(path, { confirmed: true, instruction: requested, ...generationBody(generation) }) as FocusedSchemeSnapshot;
      if (mounted.current) {
        setSnapshot(saved);
        setInstruction(saved.instruction);
        draftEdited.current = false;
        recovery.recheck();
      }
    } catch (error) {
      if (mounted.current) {
        if (isGenerationCancelled(error)) {
          setNotice(cancelledGenerationMessage);
          recovery.recheck();
        }
        else {
          setSubmissionError({ message: (error as Error).message, instruction: requested,
            savedAt: snapshot?.generated_at, previousStartedAt: previousAttempt.current });
          recovery.recheck();
        }
      }
    } finally {
      inFlight.current = false;
      if (mounted.current) setGenerating(false);
    }
  }

  const numbers = new Map(snapshot?.sources.map((source, index) => [source.id, index + 1]));
  const busy = loading || recovery.checking || active;
  return <div hidden={!visible} aria-busy={busy} className="focused-scheme">
    <AIResultHeader title="Focused Scheme" savedAt={snapshot?.generated_at} />
    <form onSubmit={event => { event.preventDefault(); void generate(); }}>
      <label>
        What should the diagram show?
        <textarea value={instruction} maxLength={2000} rows={4} required disabled={loading || active}
          placeholder="Trace how an imported conversation becomes a searchable result."
          onChange={event => { draftEdited.current = true; setInstruction(event.target.value); }} />
      </label>
      <button type="submit" className="secondary" disabled={busy || !!loadError || !instruction.trim()}>
        Generate focused scheme
      </button>
    </form>
    <small className="muted">Generation sends your instruction and selected project evidence to {generationDestination(generation.provider)}.</small>
    <ErrorBox error={loadError || recovery.error || (recoveredSubmission ? '' : submissionError?.message ?? '')} />
    {loadError && <button type="button" className="secondary" onClick={() => setRetryLoad(value => value + 1)}>Retry loading</button>}
    <Feedback>{notice || (recoveredCancellation && cancelledGenerationMessage)}</Feedback>
    {loading && !snapshot && <Feedback>Loading Focused Scheme…</Feedback>}
    {visible && active && <GenerationStatus base={base} kind="focused_scheme" label="Focused Scheme"
      sharedProgress={recovery.running ? recovery.progress : undefined} />}
    {visible && snapshot && <>
      <details className="brief-details" open={instruction.trim() !== snapshot.instruction || undefined}>
        <summary>Saved instruction</summary>
        <p className="focused-scheme-instruction">{snapshot.instruction}</p>
        {snapshot.generation && <SavedGeneration generation={snapshot.generation} />}
      </details>
      {!!snapshot.graph.nodes.length && <SchemeDiagram graph={snapshot.graph} />}
      <p><AIEmphasis text={snapshot.graph.explanation} /></p>
      {!!snapshot.graph.nodes.length && <GraphEvidence graph={snapshot.graph} numbers={numbers} prefix={prefix} sources={snapshot.sources} />}
      {!!snapshot.sources.length && <details className="scheme-sources">
        <summary>Evidence used ({snapshot.sources.length})</summary>
        <BriefSources sources={snapshot.sources} prefix={prefix} />
      </details>}
      <SchemeResultCoverage snapshot={snapshot} />
    </>}
  </div>;
}

export function Scheme({ base, generation, visible, onGenerating, onFocusedGenerating, recoveryVersion, recoveryBusy, focus, queuedFocus, onFocusChange, generationRequest = 0, onGenerationRequestHandled, waitingForKnowledge = false, onResultActivated }: GenerationRequestProps & {
  base: string; generation: Generation; visible: boolean;
  onGenerating: (active: boolean) => void; recoveryVersion: number; recoveryBusy: boolean;
  onFocusedGenerating?: (active: boolean) => void;
  waitingForKnowledge?: boolean;
  onResultActivated?: () => void;
  focus: SchemeFocus; queuedFocus: SchemeFocus; onFocusChange: (value: SchemeFocus) => void;
}) {
  const [result, setResult] = useState<SchemeResult | null>(null);
  const [view, setView] = useState<(typeof schemeViews)[number]>('Architecture');
  const [visitedFocused, setVisitedFocused] = useState(false);
  const [loading, setLoading] = useState(true);
  const [latestVersion, setLatestVersion] = useState<string | null>(null);
  const [revisionId, setRevisionId] = useState<number | null>(null);
  const [revisionLoading, setRevisionLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [generating, setGenerating] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const inFlight = useRef(false);
  const selectedRevision = useRef<number | null>(null);
  const selectingRevision = useRef(false);
  const resultRead = useRef(0);
  const alive = useRef(true);
  const path = base + '/codex/scheme';
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if ((!visible && !generationRequest) || inFlight.current || selectingRevision.current) return;
    const abort = new AbortController();
    const identity = ++resultRead.current;
    setLoading(true);
    api<SchemeResult>(path, { signal: abort.signal })
      .then((data) => {
        if (!abort.signal.aborted && resultRead.current === identity) {
          setResult(previous => selectedRevision.current !== null && previous
            ? { ...data, snapshot: previous.snapshot } : data);
          setError(''); setLatestVersion(data.snapshot?.generated_at ?? null);
        }
      })
      .catch((e: Error) => { if (!abort.signal.aborted && resultRead.current === identity) setError(e.message); })
      .finally(() => { if (!abort.signal.aborted && resultRead.current === identity) setLoading(false); });
    return () => abort.abort();
  }, [path, recoveryVersion, visible, generationRequest, refresh]);

  async function generate(requestedFocus: SchemeFocus) {
    if (inFlight.current || loading || recoveryBusy || revisionLoading || waitingForKnowledge) return;
    inFlight.current = true;
    resultRead.current++;
    setGenerating(true);
    setError('');
    setNotice('');
    onGenerating(true);
    try {
      const snapshot = await post(path, { confirmed: true, ...generationBody(generation),
        focus: requestedFocus }) as SchemeSnapshot;
      if (alive.current) {
        setLatestVersion(snapshot.generated_at);
        selectedRevision.current = null;
        setRevisionId(null);
        setResult((previous) => ({
        snapshot,
        freshness: { state: 'unknown', partial: snapshot.coverage.partial, limitations: snapshot.coverage.limitations },
        inspection: previous?.inspection ?? {
          tree: snapshot.tree, dependencies: snapshot.dependencies, coverage: snapshot.coverage, sources: snapshot.sources,
          dependency_inventory: snapshot.dependency_inventory,
        },
        }));
        try {
          const current = await api<SchemeResult>(path);
          if (alive.current) setResult(current);
        } catch {
          if (alive.current) setError('Scheme saved. Could not check source freshness.');
        }
      }
    } catch (e) {
      if (alive.current) {
        if (isGenerationCancelled(e)) setNotice(cancelledGenerationMessage);
        else setError((e as Error).message);
      }
    } finally {
      inFlight.current = false;
      if (alive.current) { setGenerating(false); onGenerating(false); }
    }
  }
  useGenerationRequest(base, generationRequest, generating || recoveryBusy || revisionLoading || waitingForKnowledge ? 'skip' : loading ? 'wait' : 'ready',
    () => generate(queuedFocus), onGenerationRequestHandled);
  const snapshot = result?.snapshot;
  const sources = snapshot?.sources ?? result?.inspection.sources ?? [];
  const prefix = base.split('/')[2] + '-scheme';
  const numbers = new Map(sources.map((source, index) => [source.id, index + 1]));
  const inspection = schemeInspectionForDisplay(snapshot, result?.inspection);
  const coverage = inspection.coverage;
  const freshness = revisionId !== null ? undefined : result
    ? `${freshnessLabels[result.freshness.state] ?? label(result.freshness.state)}${result.freshness.partial || coverage?.partial ? ' · Partial coverage' : ''}`
    : undefined;
  const graph = view === 'Workflow' ? snapshot?.workflow : snapshot?.architecture;
  const focused = view === 'Focused Scheme';
  return <section className="card scheme-panel" aria-busy={!focused && (loading || generating || recoveryBusy || revisionLoading)}>
    <nav className="tabs scheme-tabs" aria-label="Scheme views">
      {schemeViews.map((name) =>
        <button type="button" key={name} className={view === name ? 'active' : ''} aria-current={view === name ? 'page' : undefined}
          onClick={() => {
            if (name === 'Focused Scheme') setVisitedFocused(true);
            setView(name);
          }}>{name}</button>)}
    </nav>
    {visitedFocused && <FocusedScheme key={base} base={base} generation={generation} visible={focused && visible} onGenerating={onFocusedGenerating} />}
    <div className="scheme-overview" hidden={focused}>
    <AIResultHeader title="Scheme" savedAt={snapshot?.generated_at} previous={revisionId !== null}
      freshness={freshness} />
    <label>Scheme focus for next run
      <select value={focus} onChange={event => onFocusChange(event.target.value as SchemeFocus)}
        disabled={loading || generating || recoveryBusy || revisionLoading || waitingForKnowledge}>
        {(Object.keys(schemeFocusLabels) as SchemeFocus[]).map(option =>
          <option key={option} value={option}>{schemeFocusLabels[option]}</option>)}
      </select>
    </label>
    <button type="button" className="secondary" onClick={() => void generate(focus)}
      disabled={loading || generating || recoveryBusy || revisionLoading || waitingForKnowledge}>
      Generate Scheme
    </button>
    <small className="muted">Generation sends selected project evidence to {generationDestination(generation.provider)}.</small>
    <details className="brief-details">
      <summary>How focus works</summary>
      <p><strong>Balanced</strong> draws on code and discussions. <strong>Code first</strong> emphasizes how the current checkout works. <strong>Sessions first</strong> emphasizes decisions, reasons, and proposed workflows.</p>
      <p>Dependencies come from the repository scan. This choice also applies when Scheme runs after Knowledge from the Generate tab.</p>
    </details>
    {snapshot?.focus && <small>Saved focus · {schemeFocusLabels[snapshot.focus]}</small>}
    {waitingForKnowledge && <Feedback>Waiting for Knowledge…</Feedback>}
    <ErrorBox error={error} />
    <Feedback>{notice}</Feedback>
    {revisionLoading && <Feedback>Loading saved version…</Feedback>}
    {loading && !result && <Feedback>Loading Scheme…</Feedback>}
    <details className="brief-details">
      <summary>Scheme details</summary>
      {snapshot && <SavedGeneration generation={snapshot.generation} />}
      {snapshot && <SchemeKnowledgeContext context={snapshot.knowledge_context} />}
      <RevisionHistory<SchemeSnapshot> base={base} kind="scheme" version={latestVersion}
        disabled={loading || generating || recoveryBusy || waitingForKnowledge} selectedId={revisionId}
        onActivated={onResultActivated} onLoadingChange={value => {
          selectingRevision.current = value;
          if (value) { resultRead.current++; setLoading(false); }
          setRevisionLoading(value);
        }}
        onSelect={(selected, id) => {
          resultRead.current++;
          selectedRevision.current = id;
          setLoading(false);
          setResult((previous) => previous ? { ...previous, snapshot: selected,
            freshness: id !== null ? previous.freshness : { state: 'unknown', partial: selected?.coverage.partial ?? false,
              limitations: selected?.coverage.limitations ?? [] },
          } : previous);
          setRevisionId(id);
          if (id === null) {
            setLatestVersion(selected?.generated_at ?? null);
            setRefresh(value => value + 1);
          }
        }} />
    </details>
    {!loading && !snapshot && view !== 'Dependencies' && <Empty>No saved Scheme. Select Scheme on the Generate tab to create one.</Empty>}
    {!focused && view !== 'Dependencies' && graph && <>
      {!!graph.nodes.length && <SchemeDiagram graph={graph} />}
      <p><AIEmphasis text={graph.explanation} /></p>
      {!!graph.nodes.length && <>
        <small>Observed: evidence found. Behavior untested.</small>
        <GraphEvidence graph={graph} numbers={numbers} prefix={prefix} sources={sources} />
      </>}
    </>}
    {view === 'Architecture' && result && <CodeStructure entries={snapshot?.tree ?? result.inspection.tree}
      highlighted={new Set(snapshot?.architecture.nodes.flatMap((node) => node.paths) ?? [])} />}
    {view === 'Dependencies' && result && <SchemeDependencies snapshot={snapshot} current={result.inspection}
      historical={revisionId !== null} prefix={`${prefix}-dependency`} />}
    {view !== 'Dependencies' && !!sources.length && <details className="scheme-sources">
      <summary>Evidence used ({sources.length})</summary>
      <BriefSources sources={sources} prefix={prefix} />
      <details><summary>Evidence provenance</summary><ul>{sources.map((source, index) =>
        <li key={source.id}>[{index + 1}] {label(source.provenance)}{source.path ? ' · ' + source.path : ''}
          {source.revision ? ' · revision ' + source.revision.slice(0, 12) : ''}{source.date ? ' · ' + source.date : ''}</li>)}</ul></details>
    </details>}
    {result && <SchemeResultCoverage snapshot={snapshot} current={result.inspection} showCurrent={revisionId === null} />}
    </div>
  </section>;
}
