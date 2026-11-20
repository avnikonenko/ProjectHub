# Evidence-based Knowledge pipeline

Contributor reference for the Knowledge extraction and synthesis path.
Knowledge Summary and records save independently of the general Project Overview.
See [AI generation](AI_GENERATION.md) for the user workflow.

## What is implemented

- knowledge_sources.py adapts existing indexed Codex messages and imported public
  ChatGPT transcript notes. It retains roles, exact text/offsets, stable local IDs,
  hashes, within-conversation order, and separate parent manifests. No remote history
  or model call is made by this adapter. Ordinary notes and TODOs are not acceptance evidence.
- Messages above 6,000 characters become exact fragments. Extraction windows use
  the [selected profile's limits](#extraction-and-synthesis) and identify preceding
  context separately. Fragmentation can leave distant context incomplete.
- knowledge.py extracts material choices, requirements, proposals, findings, and open
  questions. Evidence quotations must match the supplied original fragment and speaker.
  User-supported acceptance needs user evidence; short replies need an earlier proposal.
  These checks establish attribution, not semantic correctness of an AI interpretation.
- Successful batches commit independently to the existing project-scoped cache.
  Keys include source and context text, revisions, schema, prompt, instructions, model, and
  reasoning effort. Account identity is included when the model or effort uses a default.
  Appending to a conversation preserves cache entries for unchanged windows.
- When a window ends with an incomplete assistant heading, include the exact next
  paragraph from the same message as bounded context when it fits. This is source
  text, not an intermediate summary. Only affected cache keys change. Heading-only
  historical interpretations are retained with a context-review note during
  reconciliation; this does not rewrite previously saved previews retroactively.
- Conservative reconciliation preserves scope and historical design status. Exact matching
  claims can aggregate reasons and evidence. Semantic variants remain separate. Supported
  chronological supersession requires the same scope and conversation; conflicts retain
  their evidence and are interpretations. A→B→A stable-identity cycles remain unresolved.
- ProjectKnowledge stores one current JSON snapshot (additive migration 0020), with history
  in existing GenerationRevision records. No legacy Decision workflow is revived.
- knowledge_brief.py synthesizes the existing brief shape from supported active knowledge
  plus independent repository evidence. Stale/unavailable/conflicting interpretations stay
  in the snapshot with coverage warnings instead of being relabeled as proposals.

## Generation and publication

`POST /api/projects/{id}/codex/knowledge/brief` requires `confirmed:true` and
accepts model and effort overrides. The server uses the existing project account
and instructions. All public Knowledge generation routes save automatically,
including the legacy `POST /api/projects/{id}/codex/analysis/preview` alias.
The legacy `publish` field is still accepted on the Knowledge route. Its value
does not disable saving.

A save updates current Knowledge, its `summary`, and a Knowledge revision in
one transaction. The snapshot and summary share a publication ID and timestamp.
General Project Overview and its revisions remain independent. The source
manifest is rechecked under a short SQLite writer transaction. Inspected
repository excerpts are also rechecked for changes.

`GET /api/projects/{id}/codex/knowledge` returns the saved snapshot, current
source manifest and coverage, `current_evidence_availability`, and whether
the result is paired with the current brief. The availability overlay leaves
historical design status intact. Legacy stored previews are readable through
`GET /api/projects/{id}/codex/knowledge/preview`. The activation endpoints
remain for compatible stored results. See [Project Knowledge](PROJECT_KNOWLEDGE_UI.md)
for the current interface.

## Extraction and synthesis

Thorough, Balanced, and Summary-assisted cap serialized extraction windows at
14,000, 18,000, and 24,000 characters respectively. Summary-assisted caps distinct source text
at 60,000 characters and reports partial coverage. All profiles distinguish
primary fragments from repeated context and use the same evidence validation.
Versioned changes to extraction instructions or schema require fresh processing
while preserving older caches.

Extraction supplies the output contract in the prompt and constrains the JSON
schema and source IDs. Application validation checks lengths, statuses, source
ownership and evidence quotations before caching. A quotation with only
whitespace differences must resolve to one unambiguous original passage.
The saved quotation retains the original text.

Final synthesis uses lossless dictionaries for repeated evidence and
request-local citation aliases. Original IDs are restored before citation
resolution. Lossless repeated-item compaction starts at 180,000 prepared-input
characters. Each synthesis request is bounded to 240,000.

## Large overview synthesis

ProjectHub sends eligible inputs that exceed one synthesis request in bounded
stages. Each stage cites original evidence. All eligible active Knowledge items,
supplied repository excerpts and required later-message context participate.
Existing source availability, conflict filtering and profile limits still apply.

Independent summaries within a stage can run in parallel. The next stage waits
for its inputs. A merge depth limit and a nonshrinking-input check bound the
process. Invalid output, unresolved references or input that cannot fit produce
an explicit error. The previous saved result and completed stage caches remain
available.

Stage cache keys include the versioned namespace, project, source inputs,
requested model and effort, and instructions. When the model or effort uses a
default, the key also includes the effective account directory.
Cache hits are revalidated. Publication uses a guarded transaction after the
complete result passes validation and freshness checks.

## Processing diagnostics

`GET /api/projects/{id}/codex/generation/status?kind=knowledge` exposes progress.
It includes `elapsed_seconds`, `stage_seconds`, batch counts and cache reuse.
The server logs stage duration without source excerpts or prompts. Per-run
counters describe completed processing in the current server process.

Knowledge records `extraction_workers` and ordered `batch_timings`. History
batch users also retain `archive_workers`, `archive_generated_batches`,
`archive_cache_hits` and `archive_batch_timings`. Synthesis records
`synthesis_workers` and ordered `synthesis_batch_timings`, with model time,
cache lookup time and local processing time. The same overall budget and
cancellation state apply to all workers and stages.

Token counts come from App Server `thread/tokenUsage/updated` notifications.
Each update replaces the previous sample for that request. Cached input and
reasoning output are breakdowns of the reported total. Partial reports indicate
missing requests or fields. Request counts count `turn/start` attempts.
A cache hit does not repeat the model cost of the run that created the cache.

## Isolated testing

Use disposable data and a separate port for live testing:

```bash
export PROJECTHUB_DATA_DIR="$(mktemp -d /tmp/projecthub-knowledge-test.XXXXXX)"
export PROJECTHUB_PORT=8770
export PROJECTHUB_HISTORY_AUTO_SYNC=false
.venv/bin/python -m backend.app.cli start
```

Open http://127.0.0.1:8770 and create a disposable project. Use **Sources** to link
a Codex session with fictional project decisions. For example, ask Codex to
compare SQLite and PostgreSQL for a fictional desktop app, then reply
`Use SQLite for the first release.` Under **History → Source details**, index
or refresh the Codex history. Check that the exchange appears in History.

To generate and save Knowledge in the isolated copy, use the test project's actual
ID and a supported model and effort:

```bash
umask 077
curl --fail-with-body http://127.0.0.1:8770/api/projects/1/codex/knowledge/brief \
  -H 'Content-Type: application/json' \
  -d '{"confirmed":true,"model":"YOUR_SUPPORTED_MODEL","effort":"low"}' \
  > /tmp/projecthub-knowledge-result.json
```

This explicitly sends selected original conversations and project evidence to the configured
Codex account and saves the result to the isolated database. Keep the returned
JSON private and outside the repository. Review the saved summary and records.

To check the included synthetic references without calling Codex, run:

```bash
.venv/bin/python -m pytest tests/test_knowledge_reference.py tests/test_knowledge_recall_reference.py
```

These tests validate fixture evidence and exercise the pipeline with simulated extraction.

## Known limitations

For reproducible comparisons, specify model and effort. If Codex inherits externally configured
defaults, changes to those defaults are not discoverable from the requested configuration alone;
diagnostics flags this limitation. No profile or credential scraping is performed.

Legacy imported transcript boundaries rely on retained role markers; ambiguous/nonalternating
or malformed boundaries are excluded. Marker parsing cannot recover missing original message IDs,
dates, or perfectly prove the origin of quoted marker-like text. Within-session order is retained;
import dates are never cross-session chronology. Source limits and exclusions are explicit.
The adapter reads bounded local originals to compare hashes; it does not provide network-level
incremental syncing beyond the existing indexer.

Decision precision/recall, actual token cost and scientific validity are not established
by source attribution or a readable overview. Human review and supported reference
cases remain necessary to measure extraction quality. Manual corrections and
advanced semantic merging remain separate work.


## Activating a saved result

For legacy saved previews, `POST /api/projects/{id}/codex/knowledge/preview/publish`
accepts a saved `publication_id` and makes no model request. The pair
publication transaction verifies the latest stored result, unchanged Overview
baseline, source manifest, literal evidence and project ownership before publishing
both current records and revisions. Exact retries are idempotent. New generated
results retain a repository inspection signature; older results without it require
regeneration for repository-backed projects. No repository files are modified.


## Material conditions in extraction

Extraction instructions require every action statement to retain its
material eligibility and state guards, including restrictions supplied in nearby
numbered policy clauses. A rule preserving records or charges does not establish
permission to release a reservation. Explicitly enumerated chargeable outcomes
remain scoped to those outcomes. The cache key includes the instruction version
independently of the schema version. A changed policy requires fresh extraction
for windows without matching cache entries.

### Bounded quotation-format retry

A fresh response can receive one additional request after an excerpt, speaker
attribution, or agreement validation failure. Agreement retries ask for a
supported status and actual user evidence. Cancellation and the overall deadline
still apply. Corrupt cache entries, foreign references, malformed fields, invalid
JSON, and transport errors fail immediately.

For that retry, application code presents the same original source text once as
ordered, bounded literal snippets. The model selects snippet IDs instead of
retyping quotes. The decoder enforces each ID's source owner and restores the
exact original text, including punctuation and source-code typos; it never uses
fuzzy matching or repairs the source. Blank snippets remain context but cannot
be selected as evidence. Canonical extraction validation runs again before save.

The retry stores the same canonical schema with literal excerpts. The cache envelope records
`quote_selection_mode` when used. `ai_requests` includes the extra reading request,
`quote_selector_retries` records it, and model/token metrics cover both calls when
usage is reported. If the retry cites the wrong speaker, an inexact excerpt, or
insufficient evidence for an agreement or rejection, the run keeps the valid
records and reports how many claims it omitted under Coverage. A future
generation can retry batches with omitted claims. Unknown selectors and malformed
responses fail validation.

### Scope checks when combining Overview stages

Combination prompts distinguish feature modes, lifecycle phases and populations
before reporting a conflict or undefined policy. Different explicitly defined
rules can coexist; unknown run configuration is a separate provenance question
and must not be invented from a scope difference alone. Original excerpts remain
primary over intermediate summary wording. This guidance is applied only when
combining summary records, preserving normal-sized prompts and reusable source
stage caches. It does not establish that every synthesized claim is semantically
correct.
