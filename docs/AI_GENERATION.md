# AI generation

ProjectHub sends selected project evidence to OpenAI when you request generation.
Browsing, searching and opening saved results use local data. Source references
help you check an AI claim against the original evidence.

## Generate or retry

Open the project's **Generation** tab. Select **Overview**, **Knowledge**,
**Scheme**, **Q&A** or **Implementation Choices & Limits**, then click
**Regenerate selected**. Results save automatically after validation.

- **Overview** summarizes the project, its architecture, current work and next
  steps. **Knowledge** saves a separate summary and records of choices,
  requirements, reasons and supporting evidence.
- **Regenerate all** starts available views in parallel. Scheme waits for a
  successful Knowledge generation when both are started together. Generating
  Scheme alone uses the currently saved Knowledge.
- **Retry unfinished** starts only missing or unsuccessful views. The latest
  attempt status appears alongside any earlier saved result.
- **Generate AI tags** is a separate action on saved Knowledge and Q&A.

Open **Previous versions** where available to read an earlier saved result.
See [Project Overview and Knowledge](PROJECT_KNOWLEDGE_UI.md) and [Scheme](SCHEME.md)
for the individual views.

## Progress, cancellation and timeouts

Progress shows the current stage, completed batches and cache reuse when available.
Use **Cancel generation** to stop a run. A cancellation may take some time to
reach an active request. Failures and cancellations preserve earlier saved results
and completed batches. A retry can reuse matching batches.

Reloading the page reconnects to an active run on the same ProjectHub server.
An interrupted run can be retried after a server restart.

Change **Settings → Overall time budget (seconds)** to set the total allowance
for the next run. The default is 900 seconds (15 minutes), with a range of 30 to
7,200 seconds. The separate per-response timeout still applies. Increase the
overall budget if a job needs more time across its stages. Changing the model or
reasoning effort may also help with requests that stall.

## Settings for quality and resource use

**Knowledge generation profile** controls how much conversation context Knowledge
reads and how it groups that context.

| Profile | When to use it |
| --- | --- |
| **Quality** (default) | Smaller batches help retain detail across the available source material. |
| **Balanced** (experimental) | Larger batches cover the available sources with potentially fewer calls. Some details may be missed. |
| **Economy** (experimental) | Larger batches and a source limit can reduce usage. Check Coverage for omitted conversation text. |

Explicit model and reasoning settings take precedence. Economy can default to
low reasoning for a selected model that supports it. Changing the profile, model
or instructions can require fresh processing.

**AI workers per job** sets the maximum number of independent batches running
in parallel. The default is 10, and 1 runs batches in sequence. Concurrent jobs
each get the configured limit. Higher values can increase memory use and account
requests. Start with the default or lower it if you hit resource or account limits.

Settings save automatically and apply to the next generation. Dropdowns save
on selection. Number fields save after a short pause, on Enter, or when you leave
the field. Startup environment overrides can take precedence after restart.
See the [configuration example](https://github.com/avnikonenko/ProjectHub/blob/main/config.example.toml). Scheme has its own
[inspection and excerpt settings](SCHEME.md#freshness-and-coverage).

## Check coverage and costs

Expand **Coverage** below a generated result.

- **Files used and missed** shows supplied excerpts and recorded omissions. A
  file can be read locally while only part of it is sent to AI.
- **Detailed coverage** shows available conversation and repository counts, source
  scope and limitations.
- **Processing diagnostics** shows timing, worker counts, cache reuse or token
  usage when recorded. Token usage can be partial and reflects the reported
  requests for that run.

Older results may have fewer recorded measurements. The counts explain what was
processed. Check the cited passages to judge whether a result accurately describes
your project.

## History sync

ProjectHub checks linked Codex history locally about once a minute by default.
Use **Pause automatic sync** in **Sources** for a session that is unavailable.
Use **History** for a manual refresh. Imported ChatGPT shares remain saved snapshots.
This sync runs locally without generating an AI result. The interval and default
sync behavior are available in the [configuration example](https://github.com/avnikonenko/ProjectHub/blob/main/config.example.toml).

Contributors can find extraction, synthesis and diagnostic details in the
[Knowledge pipeline reference](KNOWLEDGE_PIPELINE.md).
