# Evidence-based knowledge pilot

The evidence-based pipeline remains opt-in, rather than the default brief path.
Do not promote it to the default until the reference cases and live model output
have been reviewed. Q&A and Scheme are unchanged.

## What is implemented

- knowledge_sources.py adapts existing indexed Codex messages and imported public
  ChatGPT transcript notes. It retains roles, exact text/offsets, stable local IDs,
  hashes, within-conversation order, and separate parent manifests. No remote history
  or model call is made by this adapter. Ordinary notes and TODOs are not acceptance evidence.
- Messages above 6,000 characters become exact fragments. Extraction windows are
  bounded to 14,000 serialized characters, with preceding context identified separately.
  Fragmented distant context can remain incomplete and is reported as a limitation.
- knowledge.py extracts material choices, requirements, proposals, findings, and open
  questions. Evidence quotations must match the supplied original fragment and speaker.
  User-supported acceptance needs user evidence; short replies need an earlier proposal.
  These checks establish attribution, not semantic correctness of an AI interpretation.
- Successful batches commit independently to the existing project-scoped cache.
  Keys include source/context text and revisions, schema/prompt/instructions, and requested
  account/model/effort. Whole-document append timestamps do not invalidate unchanged windows.
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

## Preview and publication

POST /api/projects/{id}/codex/knowledge/brief accepts confirmed, optional model/effort,
and publish (default false). It uses the existing account and project instructions.

Preview returns knowledge, candidate brief, baseline_brief, publication_id, and diagnostics.
It preserves both published records while retaining successful extraction caches. This
allows side-by-side evaluation without replacing the current overview.
Successful previews are also stored under the project-scoped `knowledge_preview`
kind in existing generation revisions. `GET /api/projects/{id}/codex/knowledge/preview`
recovers the last successful result without generation. Saving is guarded by the
same cancellation/time-budget check; failed replacements preserve the previous
preview and published records. No separate knowledge store is introduced.

Explicit publish:true updates current Knowledge, its `summary`, and one Knowledge
revision in one transaction. Snapshot and summary share a publication ID and timestamp;
the independent general Project Overview and its revisions remain unchanged.
The source manifest is rechecked under a short SQLite writer transaction; a source or
metadata change aborts publication. Inspected repository excerpts are rechecked too,
without claiming that filesystem edits can be locked atomically with SQLite.

GET /api/projects/{id}/codex/knowledge returns the last published snapshot, current source
manifest/coverage, current_evidence_availability, and whether it is paired with the current
brief. Historical design_status is not rewritten by this read-only availability overlay.
A later legacy refresh can create a newer legacy brief; the pairing flag then becomes false.

GET /api/projects/{id}/codex/generation/status?kind=knowledge exposes pilot progress.
The ordinary /codex/brief endpoint remains the default for the Overview refresh button.
The Knowledge tab shows saved results directly. Generate/Regenerate creates an opt-in
candidate; **Save Knowledge Summary** atomically saves its Knowledge records and
matching detailed summary. The summary is stored in the published Knowledge
snapshot, with the same publication ID. General Project Overview is saved and
refreshed independently; neither save path overwrites the other perspective.
See [Project Knowledge](PROJECT_KNOWLEDGE_UI.md) for current UI behavior and limitations.

## Large overview synthesis

Eligible overview inputs that exceed one synthesis request are processed in bounded
stages, then merged with original-evidence citations. The normal single-request
path is unchanged for inputs that already fit. Staging does not change the stored
Knowledge snapshot, revive the decision workflow, or silently select fewer active
items. Existing extraction coverage and source-availability limits remain explicit.

Validated stage responses reuse the project-scoped history-batch cache with a
separate versioned namespace and input/configuration identity. Cache hits are
revalidated. Failed or cancelled runs retain successful stage caches and previous
saved results; publication remains a single guarded transaction after complete
success. See [AI generation](AI_GENERATION.md#large-overviews-and-staged-synthesis)
for progress, costs and remaining limits.

## Isolated testing

Do not use the production data directory just to try the pilot.
Start with disposable data and a different port:

```bash
export PROJECTHUB_DATA_DIR="$(mktemp -d /tmp/projecthub-knowledge-test.XXXXXX)"
export PROJECTHUB_PORT=8770
export PROJECTHUB_HISTORY_AUTO_SYNC=false
.venv/bin/python -m backend.app.cli start
```

Use http://127.0.0.1:8770 and import the four share URLs in the reference fixture, or use
an independently backed-up test copy of the application database. Migration 0020 applies
to that test data at startup. Existing Codex authentication remains outside the repository.

To request a preview, use the test project's actual ID and a supported model/effort:

```bash
umask 077
curl --fail-with-body http://127.0.0.1:8770/api/projects/1/codex/knowledge/brief \
  -H 'Content-Type: application/json' \
  -d '{"confirmed":true,"publish":false,"model":"YOUR_SUPPORTED_MODEL","effort":"low"}' \
  > /tmp/projecthub-knowledge-preview.json
```

This explicitly sends selected original conversations and project evidence to the configured
Codex account. Keep preview files private and outside the repository. Review the returned
baseline_brief, candidate brief, and knowledge items before considering publish:true.

Run automated checks with .venv/bin/python -m pytest. Tests use disposable SQLite databases
and mocked extraction/synthesis, not real Codex model inference.

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
cases remain necessary before promoting the pilot. Q&A/Scheme integration, manual
corrections and advanced semantic merging remain deferred.


## Activating a saved result

`POST /api/projects/{id}/codex/knowledge/preview/publish` accepts only a saved
`publication_id`. It invokes no Codex manager or model request. The existing pair
publication transaction verifies the latest stored result, unchanged Overview
baseline, source manifest, literal evidence and project ownership before publishing
both current records and revisions. Exact retries are idempotent. New generated
results retain a repository inspection signature; older results without it require
regeneration for repository-backed projects. No repository files are modified.


## Material conditions in extraction

Extraction instruction version 17 requires every action statement to retain its
material eligibility and state guards, including restrictions supplied in nearby
numbered policy clauses. A rule preserving records or charges does not establish
permission to release a reservation. Explicitly enumerated chargeable outcomes
must not become broader umbrella categories. The schema is unchanged. Earlier
cache entries remain intact, but their interpretation version is not reused for
new v17 extraction; the first full refresh must reread sources under that rule.

### Bounded quotation-format retry

The normal extraction request remains unchanged. Only a fresh response that fails
quotation validation can receive one additional request. Before retrying, all
nonquotation fields, source ownership, speaker and agreement checks must pass.
Cancellation and the existing overall deadline still apply. Cached corruption,
foreign references, wrong speakers, invalid JSON and transport errors do not
trigger this retry.

For that retry, application code presents the same original source text once as
ordered, bounded literal snippets. The model selects snippet IDs instead of
retyping quotes. The decoder enforces each ID's source owner and restores the
exact original text, including punctuation and source-code typos; it never uses
fuzzy matching or repairs the source. Blank snippets remain context but cannot
be selected as evidence. Canonical extraction validation runs again before save.

This is a lossless wire encoding, not another interpretation pipeline or store.
The cached JSON retains schema 3 literal excerpts and v17 interpretation policy;
older valid canonical results remain replayable. The cache envelope records
`quote_selection_mode` when used. `ai_requests` includes the extra reading request,
`quote_selector_retries` records it, and model/token metrics cover both calls when
usage is reported. A second failure preserves previous results and completed caches.

### Scope checks when combining Overview stages

Combination prompts distinguish feature modes, lifecycle phases and populations
before reporting a conflict or undefined policy. Different explicitly defined
rules can coexist; unknown run configuration is a separate provenance question
and must not be invented from a scope difference alone. Original excerpts remain
primary over intermediate summary wording. This guidance is applied only when
combining summary records, preserving normal-sized prompts and reusable source
stage caches. It does not establish that every synthesized claim is semantically
correct.
