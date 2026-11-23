# Scheme implementation reference

Scheme builds source-linked architecture and workflow diagrams from inspected
project evidence. See the [Scheme guide](SCHEME.md) for usage and settings.

## API and lifecycle

- `GET /api/projects/{id}/codex/scheme` returns the saved `snapshot`, its
  `freshness`, and the current `inspection`. It inspects local evidence and can
  reuse cached results. Opening the view makes no model request.
- `POST /api/projects/{id}/codex/scheme` requires `confirmed: true` and accepts
  optional model and reasoning overrides. Scheme sends one structured generation
  request using the selected account and project instructions.
- `GET /api/projects/{id}/codex/generation/status?kind=scheme` reports progress.

`scheme.py` validates both graphs before saving. Each graph uses the configured
node and edge limits, which default to 20 and 40. The overall response-size limit
also applies. Nodes and edges cite one to six supplied source IDs. Empty graphs
need an explanation. The application renders Mermaid from validated data with
escaped labels. The snapshot and revision save in one transaction. Invalid
output leaves the previous snapshot available.

## Evidence and coverage

`scheme_evidence.py` collects bounded excerpts from repository files and project
records, including indexed conversations. Each source carries its provenance
and a content hash. Graphs distinguish observed code, proposed designs, and
uncertain relationships. Runtime behavior requires separate validation.

Repository inspection prioritizes application entry points and feature guides.
It parses declarations statically and leaves repository files untouched. Python,
npm, and Conda declarations supply dependency names and constraints. AI can
explain the role of a known dependency. Check the target environment for installed
versions. Up to 100 declarations are retained after evidence filtering.

The Code structure display cap of 300 paths is separate from the local read
budget. Coverage distinguishes files read locally, excerpts supplied to Codex,
and up to 100 named omissions. The [budget settings](SCHEME.md#freshness-and-coverage)
describe the read, excerpt, and final package limits. Freshness compares the
available inspected evidence with the saved result. Coverage reports omissions.

## Saved Knowledge

`scheme_knowledge.py` reads the current saved Knowledge for the same project.
Bounded JSON queries consider up to 96 candidates and 12,000 source-text
characters before final evidence selection. The selection preserves status,
scope, and rationale where supplied, and keeps up to four original passages per
item. Unpublished previews are outside this selection.

Interpretations and original passages have distinct source IDs. A citation to
an interpretation must include all original passages supplied for that item.
Partial selections are disclosed. An agreed design can support a proposed
component. Observed implementation requires implementation evidence.

The saved snapshot records the Knowledge publication ID and selection counts
in `knowledge_context`. A newer publication affects freshness. Use generation
to include it in a new Scheme. Follow-up generation queued after Knowledge
verifies the expected publication before generation and again before saving.
