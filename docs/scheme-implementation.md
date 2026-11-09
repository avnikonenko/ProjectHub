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

## Phases

Phase 1 only: three views, graphs, dependencies, code tree, evidence, persistence, basic freshness.
Later: manual overrides, copying/export, advanced navigation. No external indexing engine.
