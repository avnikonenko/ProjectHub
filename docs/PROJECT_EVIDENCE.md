# Project evidence and freshness

To update the general project summary, open **Generation**, select **Overview**,
and click **Regenerate selected**. Open **Overview** to read the result. Use its
citations to check specific claims, **Coverage** to see what it considered, and
**Previous versions** to read earlier results.

## What Overview uses

Overview draws on project metadata, notes, TODOs, linked Codex history, imported
conversations, and selected excerpts from a linked Git repository. An explicit
generation request sends selected evidence to Codex. Opening the project or
checking freshness does not start AI generation or run project code or tests.

Repository inspection is local and read-only. It excludes ignored untracked paths,
submodule contents, generated directories, and common private files. To add your
own repository exclusions, set `PROJECTHUB_EVIDENCE_EXCLUDE` before startup:

```bash
export PROJECTHUB_EVIDENCE_EXCLUDE='["private/**", "data/**"]'
projecthub
```

These exclusions apply to the repository. Notes and conversations you save in
ProjectHub remain eligible sources.

## How to read freshness and coverage

**No changes detected in inspected sources** means the available evidence still
matches the saved result. If sources are unavailable or inspection reaches a
safety limit, freshness may be unknown. Changes can make a saved result stale
even while generation is running. A failed update keeps the last successful result.

Coverage describes the evidence considered. The Overview distinguishes repository
observations, reported claims, inferences, and open issues. A missing test result
means ProjectHub has no recorded result to cite.

## Shared history in forked sessions

History can collapse identical opening turns from linked session forks. The
original sessions remain available, and Search can still return each copy.
ProjectHub checks indexed conversation text and keeps conversations separate
after they diverge. Tool output and attachments are outside that comparison.

See the [architecture reference](ARCHITECTURE.md#evidence-and-history) for details
on freshness checks and shared history.
