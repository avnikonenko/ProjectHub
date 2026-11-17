# AI generation

Generation sends selected project evidence to OpenAI. Browsing and searching use
local data. Follow source links to check claims against the original evidence.

## Generate or retry

Open **Generation**, select the views you want, and click **Regenerate selected**.
Results save automatically after validation. Use **Previous versions** where
available to open earlier results.

- **Regenerate all** runs available views in parallel. When both Scheme and
  Knowledge are queued, Scheme starts after Knowledge saves successfully. Scheme
  can also run on its own using saved Knowledge, when available.
- **Retry unfinished** reruns missing or unsuccessful views. Earlier results remain
  visible alongside the latest attempt status.
- **Generate AI tags** is a separate action on saved Knowledge and Q&A.

See [Project Overview and Knowledge](PROJECT_KNOWLEDGE_UI.md) and
[Scheme](SCHEME.md) for the individual views.

## Progress, cancellation and timeouts

Progress shows the stage, completed batches and cache reuse when available.
**Cancel generation** may take time to reach an active request. A failure or
cancellation preserves the saved result and lets a retry reuse matching batches.

Reload the page to reconnect to a run on the same server. After a server restart,
retry interrupted runs.

**Settings → Overall time budget (seconds)** allows 30 to 7,200 seconds per run,
with a default of 900 (15 minutes). Each response also has a separate timeout.
Raise the overall budget if a run needs more time across stages. For stalled
requests, try a different model or reasoning effort.

## Settings for quality and resource use

**Knowledge generation profile** sets how much conversation context Knowledge
reads and how it batches that context.

| Profile | When to use it |
| --- | --- |
| **Quality** (default) | Smaller batches help retain detail across available sources. |
| **Balanced** (experimental) | Larger batches may reduce calls and miss details. |
| **Economy** (experimental) | Larger batches and a source limit can reduce usage. Check Coverage for omitted text. |

Explicit model and reasoning settings take precedence. Economy can use low
reasoning by default when the selected model supports it. Changing the profile,
model or instructions can require fresh processing.

**AI workers per job** limits parallel batches for each job. The default is 10.
Set it to 1 to run batches in sequence, or lower it if you hit memory or account
limits.

Settings save automatically and apply to the next run. Startup environment
overrides can take precedence after restart. See the
[configuration example](https://github.com/avnikonenko/ProjectHub/blob/main/config.example.toml). Scheme has separate
[inspection and excerpt settings](SCHEME.md#freshness-and-coverage).

## Check coverage and costs

Expand **Coverage** below a generated result.

- **Files used and missed** shows supplied excerpts and omissions. A local file
  read may contribute only an excerpt to generation.
- **Detailed coverage** shows conversation and repository counts, scope and limits.
- **Processing diagnostics** shows recorded timing, workers, cache reuse and
  token usage. Token totals can be partial.

Older results may have fewer measurements. Check cited passages to judge accuracy.

## History sync

ProjectHub checks linked Codex history locally about once a minute by default.
Pause a session's sync in **Sources** or refresh it manually from **History**.
Imported ChatGPT shares stay as saved snapshots. See the
[configuration example](https://github.com/avnikonenko/ProjectHub/blob/main/config.example.toml)
for sync settings.

Contributors can find extraction, synthesis and diagnostic details in the
[Knowledge pipeline reference](KNOWLEDGE_PIPELINE.md).
