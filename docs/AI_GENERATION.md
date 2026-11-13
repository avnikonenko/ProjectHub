# AI generation and parallel reading

This guide describes current settings and behavior. Historical measurements and
comparisons are kept separately in [experiments](experiments/README.md).

## Progress, cancellation, and timeouts

AI generation shows completed/total history batches, batch timing and cache reuse.
Use **Cancel generation** to stop an active request while keeping the saved result
and completed batch caches. The overall budget defaults to 900 seconds; configure
`[ai] generation_budget` (30–7200 seconds) or `PROJECTHUB_GENERATION_BUDGET`. This
is separate from the per-response `generation_timeout`. Cancellation and budget
checks are cooperative; an in-flight protocol operation can take additional time
to stop.

Refreshing the browser restores progress for an ongoing Project status or AI Q&A
request after reopening its project. It does not start another generation.
Progress recovery requires the same ProjectHub server process to remain running.

Generation status includes elapsed time and completed-stage durations
(`elapsed_seconds`, `stage_seconds`). Server logs record stage duration without
prompts or source excerpts. Knowledge extraction reports batch timing/cache reuse.
These diagnostics describe the current request; they are not a performance guarantee.

## Parallel reading

Opt-in knowledge extraction runs four independent batches concurrently by default,
using separate read-only Codex transports with the selected account, model and
reasoning effort. Set `[ai] extraction_workers = N` to choose the maximum concurrent
reading threads (`1` restores serial extraction). N must be a whole number from 1
to 2,147,483,647; actual concurrency never exceeds the available extraction batches.
`PROJECTHUB_EXTRACTION_WORKERS` overrides the file. Final synthesis remains a
single request.

You can also enter N in **Settings → Parallel AI reading threads**, then click
**Save thread count**. It saves the value in your local ProjectHub settings and
applies it to the next generation, without interrupting an active run or invoking
Codex. A launch-time environment override still takes precedence after restarting;
the control shows when the effective and saved values differ.

> **Parallel-reading warning:** Each active uncached batch can start a separate
> Codex process and request. High thread counts can exhaust memory or operating-system
> resources and encounter account usage/concurrency limits. More threads do not
> reduce model calls or token consumption and can make generation slower or fail.
> Start with the default 4 (or 1 for serial reading) and increase gradually after
> measuring; counts larger than the number of batches provide no additional concurrency.

Parallelism preserves batch inputs, cache identities and reconciliation order.
Successful batch caches survive failures; cancellation and the overall budget
apply to all workers. Diagnostics include `extraction_workers` and ordered
`batch_timings`.

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
own profile across history pages. AI batches within one refresh share a connection
while retaining separate ephemeral threads. Unchanged note chunks retain their cache
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

Final synthesis uses lossless dictionaries for repeated evidence and request-local
citation aliases. Original identities are restored before citation resolution;
saved knowledge and citation URLs retain their original IDs. This reduces
transmitted context without dropping records or increasing the synthesis limit.
See [the pipeline guide](KNOWLEDGE_PIPELINE.md) for its contracts and limitations.
