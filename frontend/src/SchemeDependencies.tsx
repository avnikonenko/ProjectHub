import { AIEmphasis } from './AIEmphasis';
import { briefReferences, BriefSources, type BriefSource } from './BriefReferences';
import { Empty, label } from './ui';

export type Dependency = {
  id: string; name: string; constraint: string; category: string; purpose: string;
  source_ids: string[]; purpose_source_ids?: string[];
};
type DependencySource = BriefSource & { provenance: string };
export type DependencyInventory = {
  dependencies: Dependency[];
  sources: DependencySource[];
  coverage: { files_total: number | null; files_scanned: number; limitations: string[] };
  repository_identity?: string;
};

type DependencyEvidence = {
  dependency_inventory?: DependencyInventory;
  saved_dependency_repository_matches?: boolean;
  dependencies: Dependency[];
  sources?: DependencySource[];
};

export function SchemeDependencies({ snapshot, current, historical, prefix }: {
  snapshot?: DependencyEvidence | null; current?: DependencyEvidence; historical: boolean; prefix: string;
}) {
  const inventory = historical ? snapshot?.dependency_inventory
    : current?.dependency_inventory ?? snapshot?.dependency_inventory;
  const legacy = historical ? snapshot : snapshot ?? current;
  const currentScan = !historical && !!current?.dependency_inventory;
  const savedRepository = snapshot?.dependency_inventory?.repository_identity;
  const canReuseSaved = !currentScan || (savedRepository
    ? savedRepository === inventory?.repository_identity
    : current?.saved_dependency_repository_matches === true);
  const sourcesById = new Map((inventory?.sources ?? legacy?.sources ?? []).map(source => [source.id, source]));
  const savedSources = new Map((canReuseSaved
    ? [...(snapshot?.dependency_inventory?.sources ?? []), ...(snapshot?.sources ?? [])] : [])
    .map(source => [source.id, source]));
  const savedPurposes = new Map((canReuseSaved
    ? [...(snapshot?.dependencies ?? []), ...(snapshot?.dependency_inventory?.dependencies ?? [])] : [])
    .filter(dependency => dependency.purpose).map(dependency => [dependency.id, dependency]));
  const savedCitationIds = new Map<string, string>();

  function savedCitation(id: string) {
    const known = savedCitationIds.get(id);
    if (known !== undefined) return [known];
    const source = savedSources.get(id);
    if (!source) return [];
    let displayId = id;
    const existing = sourcesById.get(id);
    if (existing && JSON.stringify(existing) !== JSON.stringify(source)) {
      // The same repository citation ID can refer to changed text after a scan.
      // Preserve the archived excerpt without replacing the current declaration.
      displayId = `saved:${id}`;
      for (let suffix = 2; sourcesById.has(displayId); suffix++) displayId = `saved:${id}:${suffix}`;
    }
    sourcesById.set(displayId, { ...source, id: displayId });
    savedCitationIds.set(id, displayId);
    return [displayId];
  }

  const dependencies = (inventory?.dependencies ?? legacy?.dependencies ?? []).map(dependency => {
    const saved = savedPurposes.get(dependency.id);
    if (saved && saved.name === dependency.name && saved.category === dependency.category
        && saved.constraint === dependency.constraint) {
      return { ...dependency, purpose: saved.purpose,
        purpose_source_ids: (saved.purpose_source_ids ?? []).flatMap(savedCitation), savedPurpose: currentScan };
    }
    return { ...dependency, purpose_source_ids: (dependency.purpose_source_ids ?? []).flatMap(id =>
      sourcesById.has(id) ? [id] : savedCitation(id)), savedPurpose: false };
  });
  const referenced = new Set(dependencies.flatMap(dependency => [...dependency.source_ids, ...(dependency.purpose_source_ids ?? [])]));
  const sources = [...sourcesById.values()].filter(source => referenced.has(source.id));
  const citations = new Map(sources.map((source, index) => [source.id, index + 1]));
  const references = (ids: string[]) => briefReferences(ids, citations, prefix, ids.flatMap(id => {
    const source = sourcesById.get(id);
    return source ? [source] : [];
  }));
  const development = (dependency: Dependency) => /dev|test|build/i.test(dependency.category);
  const developmentDependencies = dependencies.filter(development);
  const runtimeDependencies = dependencies.filter(dependency => !development(dependency));
  function dependencyTable(items: typeof dependencies, tableLabel: string) {
    return <div className="scheme-table-scroll" role="region" aria-label={tableLabel} tabIndex={0}>
      <table className="diagnostic-table scheme-dependencies">
        <thead><tr><th scope="col">Dependency</th><th scope="col">Declared constraint</th><th scope="col">Category</th><th scope="col">Purpose / evidence</th></tr></thead>
        <tbody>{items.map(dependency => {
          const detectedImport = dependency.category.startsWith('import:');
          const provenance = [...new Set(dependency.source_ids.flatMap(id => {
            const source = sourcesById.get(id);
            return source?.provenance ? [label(source.provenance)] : [];
          }))];
          return <tr key={dependency.id}>
            <td>{dependency.name}</td>
            <td><code>{detectedImport ? '—' : dependency.constraint || 'Not specified'}</code></td>
            <td>{detectedImport ? 'Detected import' : label(dependency.category)}</td>
            <td>
              {dependency.purpose ? <>
                {dependency.savedPurpose && <small className="muted">Saved purpose · </small>}
                <AIEmphasis text={dependency.purpose} />
                {references(dependency.purpose_source_ids ?? [])}</>
                : <div className="muted">No purpose explanation available</div>}
              <small>{detectedImport ? 'Import' : 'Declaration'}{references(dependency.source_ids)}
                {!!provenance.length && ` · ${provenance.join(' · ')}`}</small>
            </td>
          </tr>;
        })}</tbody>
      </table>
    </div>;
  }
  return <>
    {inventory && <p className="muted"><small>
      {currentScan ? 'Repository dependency scan' : 'Saved dependency scan'} ·{' '}
      {inventory.coverage.files_scanned.toLocaleString()}{
        inventory.coverage.files_total === null ? '' : ` of ${inventory.coverage.files_total.toLocaleString()}`
      } eligible files scanned. Includes supported manifests and Python imports.
    </small></p>}
    {!dependencies.length && <Empty>No dependencies found in the inspected sources.</Empty>}
    {!!runtimeDependencies.length && dependencyTable(runtimeDependencies, 'Project dependencies')}
    {!!developmentDependencies.length && <details className="scheme-development">
      <summary>Development dependencies ({developmentDependencies.length})</summary>
      {dependencyTable(developmentDependencies, 'Development dependencies')}
    </details>}
    {!!dependencies.length && <small>Declared constraints describe requested versions. Detected imports show module names used in source code.
      {' '}Installed versions and dynamically resolved dependencies require checking the environment.</small>}
    {!!inventory?.coverage.limitations.length && <details>
      <summary>Dependency scan limitations</summary>
      <ul>{inventory.coverage.limitations.map(limitation => <li key={limitation}>{limitation}</li>)}</ul>
    </details>}
    {!!sources.length && <details className="scheme-sources">
      <summary>Dependency sources ({sources.length})</summary>
      <BriefSources sources={sources} prefix={prefix} />
    </details>}
  </>;
}
