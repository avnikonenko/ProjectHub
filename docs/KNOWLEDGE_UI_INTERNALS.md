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

### Optimized tag generation (default)

Large saved results use a shared topic catalog, compact numeric inputs, and packed
output rows: each row starts with the record ID followed by zero to four topic IDs.
The parser requires every record exactly once and rejects foreign or duplicate IDs.
Tag-only threads use a short classification base instruction; project instructions,
read-only execution, denied tool approvals and strict source isolation remain.
Ordinary Overview and Q&A generation are unchanged.

Assignment batches contain up to 600 records or roughly 100,000 compact characters,
with a separate 240,000-character check on the actual full prompt. The existing
worker setting is respected. Small results that fit one legacy batch keep a single
combined catalog-and-assignment call. Set `PROJECTHUB_COMPACT_TAGS=0` and restart to
use the older strategy. No reasoning effort is lowered automatically.

An isolated complete cold run on 3,162 records at medium reasoning with the saved
eight-worker setting took **122.42 seconds**, ten model calls, and **252,473 reported
tokens**. It produced 15 topics and tagged 3,130 records; all 32 untagged records
remain accessible. Original records and source bytes were unchanged. An unchanged
repeat took **0.238 seconds with zero model calls**.

For comparison, cold four-worker observations were 299.20 seconds / 469,900 tokens
before the context optimization; 261.06 seconds / 358,648 tokens with classification
context; and 293.50 seconds / 271,009 tokens with larger batches but verbose output.
These are individual runs, with differing worker counts in the final run, not a
controlled attribution of every improvement or a universal timing guarantee.
A grouped-by-topic output experiment was rejected after strict coverage validation
caught omitted/conflicting record IDs; that format is not used.

All nine previously selected requirement cases received tags. Independent review
found some broad/weak assignments and material requirements among the untagged
records. Assigned percentage is not semantic accuracy or proof of complete recall.
Users retain All items, Untagged, original evidence, and the ability to inspect tags
as AI interpretations. Generation stays explicit and preserves the previous result
on failure or cancellation.

### Historical experimental measurements

The following measurements predate the optimized default above.

#### Original compact tagging experiment

Set `PROJECTHUB_COMPACT_TAGS=1` in the server environment and restart to try
the compact path. The existing path remains the default. The compact path
builds one shared topic catalog from a bounded sample, then classifies larger
batches using local numeric IDs, statements, and scope. It does not send original
evidence or rationale in assignment batches. A record with no strong topic match
stays available under **Untagged** for review; the assignment calls do not invent
new topics. The initial sample can miss niche topics.

Catalog and assignment caches are separate from legacy tag caches. Unchanged
items reuse their assignments when the saved catalog and generation configuration
match. Existing saved tags remain intact until a complete successful replacement.
The experiment uses the configured model and reasoning effort.

One cold comparison used the same 240 evenly spaced records from a saved
3,162-item snapshot, isolated databases, the same model, and four configured
workers. These are single observations, not full-project timing estimates.

| Path | Effort | Wall time | Calls | Reported tokens | Tagged records |
| --- | --- | ---: | ---: | ---: | ---: |
| Existing | Medium | 108.38 s | 2 | 63,001 | 240 |
| Compact | Medium | 67.81 s | 3 | 58,795 | 226 |
| Compact | Low | 57.23 s | 3 | 58,371 | 233 |

The compact runs produced 8–9 broader tags versus 35. These counts measure
coverage, not assignment accuracy. The full saved snapshot packs into 17
compact assignment batches plus one catalog call versus 27 existing batches.
A full cold run and detailed assignment review remain unverified.


Compact tagging now includes one bounded review of newly untagged records. A
48-record sample prioritizes decisions and requirements and spreads across scopes;
AI may propose up to 12 new subjects without renaming existing topics. Only the
untagged records are reassigned. This adds model calls and latency to the first
review, but unchanged reviewed records are marked so later clicks do not repeat
it. Model, instruction or review-policy changes invalidate the applicable review
markers. A record can still remain Untagged; no match is forced.

On an isolated copy of the full saved 3,162-record snapshot at medium reasoning,
the review recovered tags for 275 of 309 untagged records and increased the
catalog from 7 to 15 topics. It took 90.94 seconds, three model requests and
58,877 reported tokens. Existing nonempty assignments, original records and source
bytes remained unchanged. An unchanged repeat took 0.277 seconds and zero model
requests. The known isolation, migration and persistence requirements gained
relevant tags. Thirty-four records remain untagged, including four requirements;
coverage is not proof of semantic correctness. At the time of this earlier evaluation the path was opt-in; no
production result was modified by it.
