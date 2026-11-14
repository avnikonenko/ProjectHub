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

Explicit publish:true updates current knowledge, its revision, current brief, and its
revision in one transaction. Snapshot and brief share a publication ID and timestamp.
The source manifest is rechecked under a short SQLite writer transaction; a source or
metadata change aborts publication. Inspected repository excerpts are rechecked too,
without claiming that filesystem edits can be locked atomically with SQLite.

GET /api/projects/{id}/codex/knowledge returns the last published snapshot, current source
manifest/coverage, current_evidence_availability, and whether it is paired with the current
brief. Historical design_status is not rewritten by this read-only availability overlay.
A later legacy refresh can create a newer legacy brief; the pairing flag then becomes false.

GET /api/projects/{id}/codex/generation/status?kind=knowledge exposes pilot progress.
The ordinary /codex/brief endpoint remains the default for the Overview refresh button.
The Knowledge tab exposes published snapshots and a separate opt-in unpublished preview.
See [Project Knowledge](PROJECT_KNOWLEDGE_UI.md) for current UI behavior and limitations.

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

## Evaluation reports

The [initial pipeline evaluation](experiments/KNOWLEDGE_PIPELINE_EVALUATION.md)
preserves reference-review qualifications, the original project-specific sizing
observation and pilot follow-up notes. See also the
[reference cases](experiments/KNOWLEDGE_REFERENCE_CASES.md),
[human review rubric](experiments/KNOWLEDGE_HUMAN_REVIEW.md), and
[live pilot evaluation](experiments/KNOWLEDGE_PILOT_EVALUATION.md). Recorded request
counts and timings are observations, not token costs or accuracy scores.
