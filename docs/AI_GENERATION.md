# AI generation and parallel reading

Settings, progress, cancellation, caching, and limits for AI generation.

## Progress, cancellation, and timeouts

AI generation shows completed/total history batches, batch timing and cache reuse.
Use **Cancel generation** to stop an active request while keeping the saved result
and completed batch caches. The overall budget defaults to 900 seconds; configure
`[ai] generation_budget` (30–7200 seconds) or `PROJECTHUB_GENERATION_BUDGET`. This
is separate from the per-response `generation_timeout`. Cancellation and budget
checks are cooperative; an in-flight protocol operation can take additional time
to stop.

To change the budget without restarting, open **Settings → Overall time budget
(seconds)**, enter a whole number from 30 to 7200, and click **Save time budget**.
The default is 900 seconds (15 minutes). Saving updates your local configuration
and the next generation's allowance; an already-running request keeps its original
budget. Increasing this setting does not increase the separate per-response timeout.
It covers Project status, AI Q&A, Scheme, Knowledge generation, and Ask Project AI
responses. Ordinary Codex discussion sessions use their own lifecycle.

Refreshing the browser restores progress for an ongoing Project status or AI Q&A
request after reopening its project. It does not start another generation.
Progress recovery requires the same ProjectHub server process to remain running.

Knowledge previews also recover their active progress after a browser refresh.
The last successful unpublished preview is saved separately from published
knowledge and the project overview. Opening Knowledge loads the saved result
without calling Codex; a failed or cancelled replacement keeps
the previous result. Use **Download JSON** to inspect or share its complete
brief, knowledge and source references. A server restart stops active work but
does not erase a successfully saved preview.

Generation status includes elapsed time and completed-stage durations
(`elapsed_seconds`, `stage_seconds`). Server logs record stage duration without
prompts or source excerpts. Knowledge extraction reports batch timing/cache reuse.
These diagnostics describe the current request; they are not a performance guarantee.

## Quality and usage profiles

Open **Settings → Knowledge generation profile** and save one of these presets:

| Profile | Conversation context | Reasoning |
|---|---|---|
| Quality — default | 14,000-character batches; all available primary fragments within the existing source safety limits. | Your selected model and effort. |
| Balanced — experimental | 18,000-character batches; full available coverage, with fewer repeated context packages where possible. | Your selected model and effort. |
| Economy — experimental | 24,000-character batches; at most 60,000 distinct source characters, selected as whole context-preserving windows across conversations. | Low by default only with an explicitly selected model that advertises low effort and no explicit effort selection. |

Explicit model and reasoning choices take precedence. Codex default model keeps
its default reasoning; the profile never silently selects a different model.
Economy reports omitted primary fragments and partial coverage. Its source budget
is not a token ceiling: repeated context, instructions and final synthesis also
consume tokens. Larger batches can omit details even when all available source
fragments were sent. Neither experimental profile guarantees lower consumption or
equivalent recall.

Profiles affect the opt-in knowledge pipeline, including its generated brief.
Legacy Overview refresh, Q&A and Scheme keep their existing settings. Evidence
validation, agreement rules and project isolation are identical across profiles.
Worker count is independent: parallelism changes resource use and latency, not the
amount of source text or number of requests required by a selected profile.

The setting is `[ai] generation_profile = "quality"` in ProjectHub's local TOML;
`PROJECTHUB_GENERATION_PROFILE` is the environment override. Saving applies to the
next generation. An active request retains its captured profile and worker count.
Changing profile, model or effort can require fresh extraction; returning to
Quality reuses matching pre-profile caches when source/configuration inputs match.
Changing settings does not invoke Codex or rewrite saved results.

Expand **Coverage & technical details** under a saved result or knowledge preview for its effective
profile/effort, elapsed time, request counts, extraction-cache hits, source coverage
and reported token usage. JSON downloads retain these diagnostics. Older results
without usage remain readable and show unavailable values rather than invented
zeros. Failed/cancelled run diagnostics are also retained in generation status
while that server process remains running.

Overview, Knowledge, Q&A and Scheme use the same collapsed disclosure. Each shows
only the measurements recorded for that result; file counts are available where
the inspection recorded them. Scheme separates local file reads from files in the
AI evidence package. Current source checks are labeled separately, and older
revisions never borrow newer measurements. Opening the disclosure makes no AI call.

Token counts come from the official App Server
[`thread/tokenUsage/updated` notifications](https://learn.chatgpt.com/docs/app-server).
Repeated updates replace earlier samples for that request rather than being
summed. Partial reports identify missing requests/fields. Cached input and
reasoning output are breakdowns of the total, not additional tokens. Requests
count `turn/start` attempts; these figures are not an invoice, account balance or
measurement of plan quota. Extraction-cache creation costs from earlier runs are
not counted again when their results are reused.

## Parallel reading

The shared setting applies to opt-in Knowledge extraction and the history/evidence
batches used by legacy Project status and AI Q&A, across all projects. It defaults
to ten concurrent independent batches,
using separate read-only Codex transports with the selected account, model and
reasoning effort. Set `[ai] extraction_workers = N` to choose the maximum concurrent
reading threads (`1` restores serial batch processing). N must be a whole number from 1
to 2,147,483,647; actual concurrency never exceeds the available extraction batches.
`PROJECTHUB_EXTRACTION_WORKERS` overrides the file. Each complete job captures its
worker limit when it starts; saving a new value affects the next job. Independent
legacy reduction groups overlap within a round, while dependent rounds retain
their original order. A group is dispatched only when it is guaranteed necessary,
so parallelism does not summarize a tail that could already fit unchanged.
Independent Overview summaries within a stage and follow-up tag assignments also
share this worker limit. Dependent combining stages and Scheme generation remain
single requests.
Independent AI Q&A drafts and consolidation groups share this worker limit;
their dependent final ranking remains sequential.

You can also enter N in **Settings → Parallel AI reading threads**, then click
**Save thread count**. It saves the value in your local ProjectHub settings and
applies it to the next generation, without interrupting an active run or invoking
Codex. A launch-time environment override still takes precedence after restarting;
the control shows when the effective and saved values differ.

> **Parallel-reading warning:** Each active uncached batch can start a separate
> Codex process and request. High thread counts can exhaust memory or operating-system
> resources and encounter account usage/concurrency limits. More threads do not
> reduce model calls or token consumption and can make generation slower or fail.
> Start with the default 10 (or 1 for serial reading) and increase gradually after
> measuring; counts larger than the number of batches provide no additional concurrency.

Parallelism preserves batch inputs, cache identities and reconciliation order.
Successful batch caches survive failures; cancellation and the overall budget
apply to all workers. Diagnostics include `extraction_workers` and ordered
`batch_timings`. Legacy status/Q&A progress additionally retains `archive_workers`
(the configured limit), `archive_generated_batches`, `archive_cache_hits` and
`archive_batch_timings`. Generated batches count successful validated work; failed
request attempts are not included in that count. These diagnostics do not assert
that a large cold project will always finish within the overall time budget.

## History sync and reuse

Saved Codex history syncs locally in the background about once a minute while
ProjectHub runs. Unchanged sessions are skipped, and previous search data remains
usable if a read fails. Use **Pause automatic sync** in Sources for an unavailable
session; its saved history and requests remain intact. Manual refresh is available
in History. Sync never starts AI generation; imported ChatGPT share links remain
snapshots. To disable sync or change the interval, add `[history]` with
`auto_sync = false` or `sync_interval = 120` to the config.

Project status and AI Q&A can reuse a session's indexed text after checking current
Codex metadata, avoiding another turn-by-turn history read. Old, missing, or incomplete
indexes fall back to the history reader. A session reader stays connected to its
own profile across history pages. Parallel AI batches use isolated read-only
transports and separate ephemeral threads. Unchanged note chunks retain their cache
after small edits. The first uncached analysis of long history can still take time,
and changing the model or instructions may require new AI summaries.

## Evidence processing and limits

Versioned changes to extraction instructions or schema require fresh processing
without deleting older caches. Legacy evidence reduction stops as soon as the
remaining evidence fits its original size limits; untouched evidence and original
citations are retained. These behaviors reduce avoidable calls but do not guarantee
fast uncached generation or resolve intermittent Codex stalls.

Knowledge extraction sends the complete output contract in the prompt while
constraining JSON structure, fields and source IDs through Codex. Application
validation enforces item counts, field lengths, statuses and source ownership before
caching. A quotation selector with whitespace differences is accepted only when it
identifies one unambiguous original passage; the saved quotation is that exact source
passage. Word changes, ambiguous matches and wrong speakers remain errors.
Primary conversations are packed into bounded 14,000-character windows with
preceding same-conversation context where it fits. Primary fragments are processed
once. Smaller windows help retain independent proposals but can increase request
overhead; source limits and incomplete-context warnings remain visible. A table
quoted alongside a question is not sufficient evidence that its contents were
adopted.

Final synthesis uses lossless dictionaries for repeated evidence and request-local
citation aliases. Original identities are restored before citation resolution;
saved knowledge and citation URLs retain their original IDs. This reduces
transmitted context without dropping records.
Each overview synthesis request is bounded to 240,000 prepared-input characters;
lossless repeated-item compaction starts at 180,000. Smaller inputs keep the normal
single-request path. Larger eligible inputs use staged synthesis automatically.
See [the pipeline guide](KNOWLEDGE_PIPELINE.md) for the source contracts and limits.

### Large overviews and staged synthesis

When the full prepared overview input does not fit one request, ProjectHub divides
it into bounded groups and synthesizes each group before combining the results.
Every eligible active Knowledge item, supplied repository excerpt and required
later-message context is included in a completed stage; records are not silently
selected out to meet the final request size. Existing source-availability/conflict
filters and extraction-profile coverage limits still apply.

Intermediate results are interpretations, not new source evidence. They retain
links to exact original supporting quotations, source roles and conversation
metadata. The final overview cites the original evidence rather than the
intermediate summaries. The full Knowledge snapshot and source history remain
unchanged; a compact overview does not repeat every underlying record or establish
that every important fact was interpreted correctly.

Successful stages are cached locally using the project, original inputs, requested
model/effort, effective account directory and instruction/configuration identity. Retry can reuse completed
stages after a later failure. Changed inputs or generation settings invalidate the
corresponding stages. Changes to externally configured Codex model defaults are not
discoverable from a default-model request alone; choosing an explicit model provides
a distinct cache identity. Saved Knowledge, Overview and prior previews are replaced
only after the complete result passes validation and freshness checks.

Progress reports overview-synthesis stages separately from conversation extraction.
Coverage & technical details shows actual synthesis requests, cached stages and processed
active input counts. Independent summaries within each level run in parallel using
the configured worker limit; the next level and final combination wait for their
inputs. Diagnostics include `synthesis_workers` and ordered `synthesis_batch_timings`
with model, cache-lookup and remaining local processing times. All stages share the generation's overall
budget and cancellation state. Staging can add model calls and take longer than a
small single-request overview; caching avoids repeating unchanged successful work.

A finite merge-depth limit and a nonshrinking-input check prevent endless reduction.
An individual source that cannot fit a bounded request, malformed model output,
unresolvable references or a merge that cannot shrink produces an explicit error
without publishing a partial replacement. Completed caches and previous saved
results remain available. Increasing time or reading threads alone cannot fix an
unsplittable source or a structurally invalid model result.
