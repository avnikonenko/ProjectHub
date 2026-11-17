# Scheme MVP implementation contract

Scheme explains organization and operation; the Dashboard continues to explain project state.
No external code-intelligence service, new AI provider, checkpoints, or manual diagram editing.

## Contracts and ownership

- Evidence agent owns backend/app/integrations/codex/scheme_evidence.py and focused evidence tests.
  collect_evidence(engine, project_id) returns sources, dependencies, tree, coverage, fingerprint,
  diagnostics. Sources: id,label,text,provenance,content_hash plus optional path/revision/date.
  Dependencies: id,name,constraint,category,source_ids,purpose (initially empty).
  Tree: [{path,type: file|directory}]. Coverage: partial,limitations,files_inspected,conversation_excerpts.
  Evidence uses bounded repository inspection and existing stored/indexed discussions, never AI/history archive.
  Cache deterministic results using existing project-scoped ProjectHistoryBatch storage where useful.
- Scheme backend agent owns scheme.py, models.py, migration 0019_project_scheme.py, focused graph/service tests.
  read_scheme(db,pid) returns snapshot or null. refresh_scheme(engine,pid,manager) calls evidence once,
  one manager.generate_brief with structured schema, validates sources/endpoints, renders Mermaid,
  saves ProjectScheme.snapshot and existing GenerationRevision(kind=scheme) atomically.
  Snapshot: generated_at,architecture,workflow,dependencies,sources,tree,coverage,fingerprint,
  generation,diagnostics. Graph: nodes,edges,explanation,mermaid.
  Node: id,label,description,type,status(observed|proposed|uncertain),source_ids,paths.
  Edge: source,target,label,type,status,source_ids.
  Graph bounds: 20 nodes,40 edges. Empty graph needs explanatory text; malformed output preserves old snapshot.
  Dependency names/constraints remain deterministic; AI may explain only existing dependency IDs.
  Mermaid constructed by application code with escaped labels and no HTML/directives/links.
- Frontend agent owns Scheme.tsx plus App.tsx wiring and Scheme CSS only.
  GET {base}/codex/scheme returns {snapshot, freshness:{state,partial,limitations}, inspection:{tree,dependencies,coverage}}.
  POST same endpoint accepts {confirmed:true,model?,effort?}, returns snapshot.
  Existing GET {base}/codex/generation/status?kind=scheme supplies progress.
  Exactly Architecture,Workflow,Dependencies; code tree collapsed beneath Architecture.
  Strict Mermaid rendering, bounded errors, no AI on load; progress survives tab switches/page reload.
- Coordinator owns router.py integration, revision/progress kind integration, docs, independent QA.

## Evidence and performance

Sources are bounded excerpts actually supplied to Codex with content hashes and source identifiers.
Repository code, manifests, docs, conversation proposals remain separate evidence provenance.
Coverage and freshness are independent. No changes detected means only inspected inputs match.
No entire-history AI summary pipeline. One explicit Codex request; no automatic repairs.
Use existing selected account/model/effort/instructions and read-only restrictions.
Successful snapshots/revisions preserve evidence and remain readable without Codex.

### Repository excerpt selection

Inspection prioritizes executable modules declared by literal Python entrypoints
in `setup.py` and `pyproject.toml`, and retains README feature sections instead of
only introductory badges. Repository excerpts receive a bounded share of the
input budget alongside discussions. Setup files are parsed statically and never
executed; dynamic declarations and targets outside inspected paths may be omitted.
Project guides under `docs` and `documentation` contribute their original purpose
and section outline, including short command-help descriptions. Guides mentioning
declared commands take priority over peripheral documentation. Backup and archive
paths do not displace current application evidence, but remain in the inventory.

The Code structure display caps the tree at 300 paths independently of file
inspection. Coverage distinguishes local read stops from exclusions made while
packing AI excerpts. An omission count can include multiple excerpts from the
same file. The named omission list shows up to 100 eligible paths and their reasons.

## Phases

Phase 1 only: three views, graphs, dependencies, code tree, evidence, persistence, basic freshness.
Later: manual overrides, copying/export, advanced navigation. No external indexing engine.

## Capability and dependency regression check — 2026-11-17

An earlier evaluation emphasized CLI entrypoints and generic workflow artifacts.
Inspection recognized requirements files but omitted Conda environment declarations,
and did not select most project guides. Backup modules could also compete with current
application code. These were evidence-selection and presentation problems, not a
reason to revert the broader repository inspection.

Scheme now reads bounded project-guide outlines and original CLI-purpose prose,
prioritizes guides mentioning declared commands, and places historical manifests
after current sources. Dependency limiting happens after source-budget filtering,
so discarded historical evidence cannot consume current dependency slots. Literal
Conda/pip environment declarations retain variant-specific constraints and sources;
unsupported YAML is reported rather than interpreted. Diagram instructions prefer
domain capabilities and reserve command/module details for descriptions and paths.

An isolated end-to-end Codex check retained documented analysis capabilities and
environment-specific dependency constraints. Proposed extensions remained distinct
from supported observations, and all generated references passed validation.

The full backend regression suite passed: 1,627 tests. Focused additions cover guide
selection beyond prefixes, command-guide retention under output limits, archive
priorities, environment parsing/security, variant provenance, cache invalidation and
CI/docs/build categorization. No frontend changes were needed. This is one real
generation and static declaration validation, not proof of exhaustive architecture
recall, installed dependencies or successful execution of project workflows. Existing
inspection/text budgets and the 100-declaration ceiling still apply with coverage
limitations recorded. Saved Schemes require explicit regeneration to use the fix.

## Published Knowledge integration — 2026-11-17

`scheme_knowledge.collect_published_knowledge(db, project_id)` reads only the
project's published `ProjectKnowledge` row. Bounded SQLite JSON projections select
up to 96 candidates and at most 12,000 source-text characters; the complete
snapshot is not deserialized into Python or sent to Codex. Selection balances
design statuses, favors records marked valid and nonsuperseded in the publication, and retains
scope, rationale and recorded relationships without rewriting saved Knowledge.

Interpretations and exact original passages have distinct namespaced source IDs.
Source navigation is copied only after project ownership checks on notes, sessions
and turns. Historical extraction status remains separate from current quotation
availability. Items with six references can use a disclosed selection of four;
omitted references do not establish additional claims. A graph citation to an
interpretation must also cite its included originals. Both text and serialized
package budgets preserve this evidence closure. Repository inspection keeps its
existing share of the source budget.

`knowledge_context` records publication identity and final selection counts in the
saved Scheme and revision, and is visible under collapsed Scheme details. The
publication identity participates in inspection caching and final freshness even
when no Knowledge item fits. A new publication never triggers generation or
rewrites a saved Scheme. Missing Knowledge and old Scheme snapshots remain
supported. Invalid data and a publication change during inspection discard the
Knowledge context with a limitation rather than mixing versions. No migrations,
dependencies, new extraction pipeline or additional model calls were introduced.

A live check on isolated data confirmed that proposed operations cited original
passages supplied through Knowledge, while observed operations used repository
evidence. All references validated, and a later inspection reused its cache.

Regression cases cover project isolation, preview exclusion, publication-aware
freshness without model calls, original-source ownership, evidence closure at both
budgets, partial reference disclosure, malformed metadata and publication races.
A 3,000-item fixture with an additional 20 MiB body verifies bounded Python
allocations; that measurement excludes SQLite/native memory. The real run is one
sample, not evidence of complete architectural recall. Knowledge remains a derived
interpretation and original-source access remains independent.
