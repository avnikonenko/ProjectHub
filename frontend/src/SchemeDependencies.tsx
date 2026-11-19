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
};

type DependencyEvidence = {
  dependency_inventory?: DependencyInventory;
  dependencies: Dependency[];
  sources?: DependencySource[];
};

export function SchemeDependencies({ snapshot, current, historical, prefix }: {
  snapshot?: DependencyEvidence | null; current?: DependencyEvidence; historical: boolean; prefix: string;
}) {
  const inventory = historical ? snapshot?.dependency_inventory
    : current?.dependency_inventory ?? snapshot?.dependency_inventory;
  const legacy = historical ? snapshot : snapshot ?? current;
  const dependencies = inventory?.dependencies ?? legacy?.dependencies ?? [];
  const sourcesById = new Map((inventory?.sources ?? legacy?.sources ?? []).map(source => [source.id, source]));
  if (inventory) {
    const savedSources = new Map(snapshot?.sources?.map(source => [source.id, source]));
    for (const dependency of dependencies) {
      for (const id of dependency.purpose_source_ids ?? []) {
        const source = savedSources.get(id);
        if (source && !sourcesById.has(id)) sourcesById.set(id, source);
      }
    }
  }
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
  function dependencyTable(items: Dependency[], tableLabel: string) {
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
              {dependency.purpose ? <><AIEmphasis text={dependency.purpose} />
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
      {historical ? 'Saved dependency scan' : 'Repository dependency scan'} ·{' '}
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
