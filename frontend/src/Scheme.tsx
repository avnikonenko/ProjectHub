import { useEffect, useId, useRef, useState } from 'react';
import { api, post } from './api';
import { briefReferences, BriefSources, type BriefSource } from './BriefReferences';
import { SavedGeneration, isGenerationCancelled, cancelledGenerationMessage, type GenerationProvenance } from './GenerationStatus';
import { Empty, ErrorBox, label } from './ui';
import { RevisionHistory } from './RevisionHistory';
import { decodeSchemeText } from './SchemeText';

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
type Dependency = { id: string; name: string; constraint: string; category: string; purpose: string; source_ids: string[]; purpose_source_ids?: string[] };
type TreeEntry = { path: string; type: 'file' | 'directory' };
type Coverage = { partial: boolean; limitations: string[]; files_inspected: number; conversation_excerpts: number };
type SchemeSnapshot = {
  generated_at: string; generation?: GenerationProvenance | null;
  architecture: SchemeGraph; workflow: SchemeGraph; dependencies: Dependency[];
  sources: SchemeSource[]; tree: TreeEntry[]; coverage: Coverage; fingerprint: string;
};
type SchemeResult = {
  snapshot: SchemeSnapshot | null;
  freshness: { state: string; partial: boolean; limitations: string[] };
  inspection: { tree: TreeEntry[]; dependencies: Dependency[]; coverage: Coverage; sources?: SchemeSource[] };
};

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
      svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', 'Project scheme diagram');
      // Keep zoom outside Mermaid rendering and allow Fit on narrow screens.
      svg.style.width = '100%';
      svg.style.minWidth = '0';
      svg.style.maxWidth = 'none';
      svg.style.height = 'auto';
      element.replaceChildren(document.importNode(svg, true));
      fitDiagram();
    }).catch(() => { if (active) setError('The diagram could not be rendered. Its components and evidence are available below.'); })
      .finally(() => { if (active) setRendering(false); });
    return () => { active = false; };
  }, [graph.mermaid, graph.nodes.length, id, theme]);
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
    {rendering && <p role="status">Rendering diagram…</p>}
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
    {entries.length ? branch(root) : <p>No inspected repository files.</p>}
  </details>;
}

function GraphEvidence({ graph, numbers, prefix, sources }: { graph: SchemeGraph; numbers: Map<string, number>; prefix: string; sources: BriefSource[] }) {
  const nodes = new Map(graph.nodes.map((node) => [node.id, node.label]));
  return <details className="scheme-components">
    <summary>Components and relationships ({graph.nodes.length})</summary>
    <ul>{graph.nodes.map((node) => <li key={node.id}>
      <strong>{node.label}</strong> <span className="badge">{label(node.status)}</span>
      <p>{node.description}{briefReferences(node.source_ids, numbers, prefix, sources)}</p>
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

export function Scheme({ base, generation, visible, onGenerating, recoveryVersion, recoveryBusy }: {
  base: string; generation: { model: string; effort: string }; visible: boolean;
  onGenerating: (active: boolean) => void; recoveryVersion: number; recoveryBusy: boolean;
}) {
  const [result, setResult] = useState<SchemeResult | null>(null);
  const [view, setView] = useState<'Architecture' | 'Workflow' | 'Dependencies'>('Architecture');
  const [loading, setLoading] = useState(true);
  const [latestVersion, setLatestVersion] = useState<string | null>(null);
  const [revisionId, setRevisionId] = useState<number | null>(null);
  const [revisionLoading, setRevisionLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const inFlight = useRef(false);
  const alive = useRef(true);
  const path = base + '/codex/scheme';
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!visible) return;
    const abort = new AbortController();
    setLoading(true);
    api<SchemeResult>(path, { signal: abort.signal })
      .then((data) => {
        if (!abort.signal.aborted) {
          setResult(data); setError(''); setLatestVersion(data.snapshot?.generated_at ?? null); setRevisionId(null);
        }
      })
      .catch((e: Error) => { if (!abort.signal.aborted) setError(e.message); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [path, recoveryVersion, visible]);

  async function generate() {
    if (inFlight.current || loading || recoveryBusy || revisionLoading) return;
    inFlight.current = true;
    setGenerating(true);
    setError('');
    setNotice('');
    onGenerating(true);
    try {
      const snapshot = await post(path, { confirmed: true, model: generation.model || null, effort: generation.effort || null }) as SchemeSnapshot;
      if (alive.current) {
        setLatestVersion(snapshot.generated_at);
        setRevisionId(null);
        setResult((previous) => ({
        snapshot,
        freshness: { state: 'unknown', partial: snapshot.coverage.partial, limitations: snapshot.coverage.limitations },
        inspection: previous?.inspection ?? { tree: snapshot.tree, dependencies: snapshot.dependencies, coverage: snapshot.coverage },
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
  const snapshot = result?.snapshot;
  const sources = snapshot?.sources ?? result?.inspection.sources ?? [];
  const prefix = base.split('/')[2] + '-scheme';
  const numbers = new Map(sources.map((source, index) => [source.id, index + 1]));
  const coverage = snapshot?.coverage ?? result?.inspection.coverage;
  const graph = view === 'Workflow' ? snapshot?.workflow : snapshot?.architecture;
  const dependencies = (snapshot?.dependencies ?? result?.inspection.dependencies ?? []).filter((dependency) =>
    [dependency.name, dependency.category, dependency.purpose].join(' ').toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const development = (dependency: Dependency) => /dev|test|build/i.test(dependency.category);
  function dependencyTable(items: Dependency[]) {
    return <div className="scheme-table-scroll"><table className="scheme-dependencies">
      <thead><tr><th>Dependency</th><th>Declared constraint</th><th>Category</th><th>Purpose / evidence</th></tr></thead>
      <tbody>{items.map((dependency) => <tr key={dependency.id}>
        <td>{dependency.name}</td><td><code>{dependency.constraint || 'Not specified'}</code></td>
        <td>{label(dependency.category)}</td><td>{dependency.purpose || 'Purpose not explained yet.'}
          {briefReferences(dependency.purpose_source_ids ?? [], numbers, prefix, sources)}
          <small>Declaration{briefReferences(dependency.source_ids, numbers, prefix, sources)} · {[...new Set(dependency.source_ids.map((id) => sources.find((source) => source.id === id)?.provenance).filter(Boolean))].map((value) => label(value!)).join(' · ')}</small>
        </td>
      </tr>)}</tbody>
    </table></div>;
  }
  return <section className="card scheme-panel" aria-busy={loading || generating || recoveryBusy || revisionLoading}>
    <div className="section-title">
      <div><h2>Scheme</h2>
        {snapshot && <small>Generated {new Date(snapshot.generated_at).toLocaleString()}</small>}
        {snapshot && <SavedGeneration generation={snapshot.generation} />}
      </div>
      <button disabled={loading || generating || recoveryBusy || revisionLoading} onClick={() => void generate()}>
        {generating || recoveryBusy ? 'Generating…' : snapshot ? 'Refresh Scheme' : 'Generate Scheme'}
      </button>
    </div>
    <p className="brief-note">Architecture, workflows, and dependencies interpreted from inspected code and saved discussions. Generation sends selected evidence to Codex.</p>
    {result && <div className="scheme-freshness">
      <span>{revisionId !== null ? 'Previous saved version' : freshnessLabels[result.freshness.state] ?? label(result.freshness.state)}</span>
      {(result.freshness.partial || coverage?.partial) && <span className="badge">Partial coverage</span>}
      <details><summary>Coverage</summary>
        {coverage && <p>{coverage.files_inspected} files inspected · {coverage.conversation_excerpts} conversation excerpts included</p>}
        <ul>{[...new Set([...(coverage?.limitations ?? []), ...result.freshness.limitations])].map((item) => <li key={item}>{item}</li>)}</ul>
      </details>
    </div>}
    <RevisionHistory<SchemeSnapshot> base={base} kind="scheme" version={latestVersion}
      disabled={loading || generating || recoveryBusy} selectedId={revisionId} onLoadingChange={setRevisionLoading}
      onSelect={(selected, id) => {
        setResult((previous) => previous ? { ...previous, snapshot: selected } : previous);
        setRevisionId(id);
      }} />
    <ErrorBox error={error} />
    {notice && <p role="status">{notice}</p>}
    <nav className="tabs scheme-tabs" aria-label="Scheme views">
      {(['Architecture', 'Workflow', 'Dependencies'] as const).map((name) =>
        <button type="button" key={name} className={view === name ? 'active' : ''} aria-current={view === name ? 'page' : undefined}
          onClick={() => setView(name)}>{name}</button>)}
    </nav>
    {loading && !result && <Empty>Loading Scheme…</Empty>}
    {!loading && !snapshot && view !== 'Dependencies' && <Empty>No saved scheme. Generate one from this project’s available evidence.</Empty>}
    {view !== 'Dependencies' && graph && <>
      {!!graph.nodes.length && <SchemeDiagram graph={graph} />}
      <p>{graph.explanation}</p>
      {!!graph.nodes.length && <>
        <small>Observed means supported by inspected evidence; it does not establish that the functionality works.</small>
        <GraphEvidence graph={graph} numbers={numbers} prefix={prefix} sources={sources} />
      </>}
    </>}
    {view === 'Architecture' && result && <CodeStructure entries={snapshot?.tree ?? result.inspection.tree}
      highlighted={new Set(snapshot?.architecture.nodes.flatMap((node) => node.paths) ?? [])} />}
    {view === 'Dependencies' && <>
      <label>Search dependencies<input type="search" value={search} onChange={(event) => setSearch(event.target.value)}
        placeholder="Name, category, or purpose…" /></label>
      {!dependencies.length && <Empty>No matching direct dependencies in the inspected evidence.</Empty>}
      {!!dependencies.filter((item) => !development(item)).length && dependencyTable(dependencies.filter((item) => !development(item)))}
      {!!dependencies.filter(development).length && <details className="scheme-development">
        <summary>Development dependencies ({dependencies.filter(development).length})</summary>
        {dependencyTable(dependencies.filter(development))}
      </details>}
      <small>Constraints are declarations, not installed versions. Imports do not establish a working integration.</small>
    </>}
    {!!sources.length && <details className="scheme-sources">
      <summary>Evidence used ({sources.length})</summary>
      <BriefSources sources={sources} prefix={prefix} />
      <details><summary>Evidence provenance</summary><ul>{sources.map((source, index) =>
        <li key={source.id}>[{index + 1}] {label(source.provenance)}{source.path ? ' · ' + source.path : ''}
          {source.revision ? ' · revision ' + source.revision.slice(0, 12) : ''}{source.date ? ' · ' + source.date : ''}</li>)}</ul></details>
    </details>}
  </section>;
}
