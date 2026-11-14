# Knowledge interface internals

Contributor reference. For everyday use, see [Overview and Knowledge](PROJECT_KNOWLEDGE_UI.md).

## API and implementation

- `GET /api/projects/{id}/codex/knowledge`: published snapshot plus current evidence
  availability and current coverage. No model call.
- `POST /api/projects/{id}/codex/knowledge/brief`: explicitly generate a saved candidate.
- `POST /api/projects/{id}/codex/knowledge/preview/publish`: activate the selected saved
  publication ID without generation; return 409 for stale/conflicting results.
- `GET /api/projects/{id}/codex/knowledge/preview`: last successful unpublished
  result, or null. Read-only and project-scoped; no model call.
- `GET /api/projects/{id}/codex/generation/status?kind=knowledge`: preview progress.
- Existing conversation/transcript hash resolvers open original evidence.

Publication pairing rejects missing, empty or whitespace publication IDs when
calculating `paired_with_current_brief`. The legacy Overview refresh still uses
`/codex/brief`; the knowledge pipeline remains opt-in.

| File | Responsibility |
|---|---|
| `frontend/src/App.tsx` | Knowledge tab, Overview label, project-scoped hash navigation and mounted preview state. |
| `frontend/src/ProjectBrief.tsx` | Existing brief controls plus matching Important choices. |
| `frontend/src/BriefContent.tsx` | Shared existing brief section renderer and preview sources. |
| `frontend/src/ImportantKnowledge.tsx` | Read-only publication-checked Overview highlights. |
| `frontend/src/knowledge.ts` | API types/validation, filtering, highlight selection, links and publication matching. |
| `frontend/src/KnowledgeItems.tsx` | Shared read-only record browser, evidence and relationships. |
| `frontend/src/KnowledgePage.tsx` | Current and generated results, explicit activation and project-scoped recovery. |
| `frontend/src/knowledge.css` | Responsive filters/details and keyboard focus styling. |
| `frontend/tests/knowledge.test.ts` | Helper regressions for filtering, identity, status preservation and source links. |
| `backend/app/integrations/codex/router.py` | Project knowledge API and publication identity pairing. |
| `backend/tests/integrations/test_knowledge_api.py` | Missing/blank/mismatched/matching publication cases. |

Abort controllers and keyed render state prevent late responses from a previous
project/publication appearing under the current project.


## Remaining limits

- Interface tests cover display and navigation; extraction accuracy needs separate evaluation.
- Knowledge dropdowns use exact status/category/scope values. Project-wide search
  is separate and provides no semantic retrieval or scope equivalence inference.
- The current published snapshot is browsable; historical mismatches are reported
  rather than automatically resolving historical knowledge revisions.
- Failed or cancelled generation preserves the last successful saved preview and published data; only a complete validated result replaces the saved preview.
- Source navigation depends on stored descriptors and current availability. Saved
  excerpts remain accessible when original navigation cannot be offered.

Use an isolated data directory and port for development as described in
[the pipeline guide](KNOWLEDGE_PIPELINE.md#isolated-testing).

## Topic metadata

The explicit `/codex/tags` action updates only the selected saved result’s tag
metadata, using publication ID or Q&A generation timestamp plus atomic freshness
checks. Main Knowledge and Q&A generation never invokes tagging. Tag generation
uses project/configuration-scoped caches and bounded batches,
separate from extraction caches and Overview selection. Unchanged memberships
can be reused; successful batch caches survive later failures. Memberships are
validated against existing record IDs. Q&A deletion prunes current memberships
while preserving historical revisions. The project taxonomy is bounded to 400 topics, with up to 40 new topics per reply;
excess topics cause explicit failure rather than silent assignment truncation.
