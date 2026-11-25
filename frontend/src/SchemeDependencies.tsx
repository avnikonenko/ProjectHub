import { AIEmphasis } from './AIEmphasis';
import { briefReferences, BriefSources, type BriefSource } from './BriefReferences';
import { Empty, label } from './ui';

export type Dependency = {
  id: string; name: string; constraint: string; category: string; purpose: string;
  source_ids: string[]; purpose_source_ids?: string[]; purpose_kind?: 'package_role' | 'project_use';
};
type DependencySource = BriefSource & { provenance: string; path?: string };
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

function dependencyCategoryLabel(category: string) {
  if (category === 'environment:conda') return 'Conda environment';
  if (category === 'environment:pip') return 'Pip environment';
  return label(category);
}

function dependencyEcosystem(dependency: Dependency, sources: Map<string, DependencySource>) {
  if (dependency.category === 'environment:conda') return 'conda';
  if (dependency.category === 'environment:pip') return 'python';
  if (dependency.category.startsWith('import:')) return dependency.category;
  const manifests = dependency.source_ids.flatMap(id => {
    const source = sources.get(id);
    return source?.provenance === 'dependency manifest' ? [source.path ?? source.label] : [];
  });
  if (!manifests.length) return dependency.id;
  return manifests.some(path => path.split('/').at(-1)?.toLowerCase() === 'package.json') ? 'npm' : 'python';
}

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
        && saved.constraint === dependency.constraint && (!dependency.purpose || saved.purpose_kind !== 'package_role')) {
      return { ...dependency, purpose: saved.purpose, purpose_kind: saved.purpose_kind,
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
  const groups = new Map<string, typeof dependencies>();
  for (const dependency of dependencies) {
    const key = JSON.stringify([dependency.name, dependencyEcosystem(dependency, sourcesById)]);
    const group = groups.get(key);
    if (group) group.push(dependency);
    else groups.set(key, [dependency]);
  }
  const grouped = [...groups];
  const groupsByName = new Map<string, number>();
  for (const [, [dependency]] of grouped) {
    groupsByName.set(dependency.name, (groupsByName.get(dependency.name) ?? 0) + 1);
  }
  const development = ([, items]: typeof grouped[number]) =>
    items.every(dependency => /^(development|build|test)(:|$)/i.test(dependency.category));
  const developmentDependencies = grouped.filter(development);
  const runtimeDependencies = grouped.filter(group => !development(group));

  function declaration(dependency: Dependency) {
    const provenance = [...new Set(dependency.source_ids.flatMap(id => {
      const source = sourcesById.get(id);
      return source?.provenance ? [label(source.provenance)] : [];
    }))];
    return <small>{dependency.category.startsWith('import:') ? 'Import' : 'Declaration'}{references(dependency.source_ids)}
      {!!provenance.length && ` · ${provenance.join(' · ')}`}</small>;
  }

  function dependencyTable(items: typeof grouped, tableLabel: string) {
    return <div className="scheme-table-scroll" role="region" aria-label={tableLabel} tabIndex={0}>
      <table className="diagnostic-table scheme-dependencies">
        <thead><tr><th scope="col">Dependency</th><th scope="col">Declared constraint</th><th scope="col">Category</th><th scope="col">Purpose / evidence</th></tr></thead>
        <tbody>{items.map(([key, declarations]) => {
          const first = declarations[0];
          const detectedImport = first.category.startsWith('import:');
          const categories = [...new Set(declarations.map(dependency => dependencyCategoryLabel(dependency.category)))];
          const constraints = new Set(declarations.map(dependency => dependency.constraint).filter(Boolean));
          const unpinned = declarations.filter(dependency => !dependency.constraint).length;
          const mixed = constraints.size > 0 && unpinned > 0;
          const ecosystem = dependencyEcosystem(first, sourcesById);
          const showEcosystem = (groupsByName.get(first.name) ?? 0) > 1 &&
            (ecosystem === 'python' || ecosystem === 'npm');
          const purposes = new Map<string, typeof dependencies>();
          for (const dependency of declarations) {
            if (!dependency.purpose) continue;
            const purposeKey = JSON.stringify([dependency.purpose_kind ?? 'project_use', dependency.purpose]);
            const matching = purposes.get(purposeKey);
            if (matching) matching.push(dependency);
            else purposes.set(purposeKey, [dependency]);
          }
          const orderedPurposes = [...purposes.values()].sort(([a], [b]) =>
            Number(a.purpose_kind === 'package_role') - Number(b.purpose_kind === 'package_role'));
          function purpose(entries: typeof dependencies) {
            const [entry] = entries;
            return <>
              {entry.purpose_kind === 'package_role' ? <small className="muted">Package role · </small>
                : entries.every(item => item.savedPurpose) && <small className="muted">Saved purpose · </small>}
              <AIEmphasis text={entry.purpose} />
              {references([...new Set(entries.flatMap(item => item.purpose_source_ids ?? []))])}
              {entry.purpose_kind !== 'package_role' && entries.length < declarations.length &&
                <details className="scheme-declarations">
                  <summary>Applies to {entries.length} of {declarations.length} declarations</summary>
                  <ul>{entries.map(dependency => <li key={dependency.id}>
                    <code>{dependency.constraint || 'Not specified'}</code>
                    {declaration(dependency)}
                  </li>)}</ul>
                </details>}
            </>;
          }
          return <tr key={key}>
            <td>{first.name}</td>
            <td>{declarations.length === 1 ? <code>{detectedImport ? '—' : first.constraint || 'Not specified'}</code>
              : <details className="scheme-declarations">
                <summary>{mixed || constraints.size > 1
                  ? `${constraints.size} declared constraint${constraints.size === 1 ? '' : 's'}${mixed ? ` · ${unpinned} unpinned` : ''}`
                  : <><code>{detectedImport ? '—' : first.constraint || 'Not specified'}</code>{' '}
                    <small>({declarations.length} {detectedImport ? 'imports' : 'declarations'})</small></>}</summary>
                <ul>{declarations.map(dependency => <li key={dependency.id}>
                  {!detectedImport && <code>{dependency.constraint || 'Not specified'}</code>}
                  {categories.length > 1 && ` · ${dependencyCategoryLabel(dependency.category)}`}
                  {declaration(dependency)}
                </li>)}</ul>
              </details>}
            </td>
            <td>{detectedImport ? 'Detected import' : categories.join(', ')}
              {showEcosystem && <small>{ecosystem === 'python' ? 'Python' : 'npm'}</small>}
            </td>
            <td>
              {!!orderedPurposes.length && purpose(orderedPurposes[0])}
              {orderedPurposes.length > 1 && <details>
                <summary>More explanations ({orderedPurposes.length - 1})</summary>
                <ul>{orderedPurposes.slice(1).map(entries => <li key={entries[0].id}>{purpose(entries)}</li>)}</ul>
              </details>}
              {declarations.length === 1 && declaration(first)}
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
