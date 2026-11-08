# Project evidence and brief freshness

ProjectHub collects evidence locally when the dashboard checks brief freshness. This does not initialize Codex, generate AI text, execute project code, or run tests.

## Evidence collected

- User-supplied objective, project metadata, notes, and TODOs.
- Saved, indexed turns from currently linked Codex sessions, plus imported conversation notes.
- Git branch, HEAD, recent commit metadata, visible modified/untracked paths, and relevant filenames for documentation, tests, and dependency manifests.
- Bounded hashes of visible changed files for detecting another edit to the same path. File contents are not included in repository evidence or sent as source code.

Git inspection uses argument arrays, read-only settings, output/time limits, and disables configured filters and hooks. It supports Git worktrees. Git-ignored paths, submodule contents, generated directories, and common private-file patterns are omitted. No remote fetch or project test command is run.

Set PROJECTHUB_EVIDENCE_EXCLUDE before startup to a JSON array of additional glob patterns, for example:

    export PROJECTHUB_EVIDENCE_EXCLUDE='["private/**", "data/**"]'
    projecthub

This supplements built-in exclusions. It applies to repository evidence, not notes or conversations explicitly saved in ProjectHub.

## Evidence is not validation

Repository metadata and filenames establish only those observations. They do not prove implementation correctness. Conversation excerpts, TODOs, and notes are reported claims or plans. ProjectHub currently has no independent test-run ingestion; the evidence snapshot explicitly states that no actual test execution or scientific-validation result is available.

## Freshness

A stable fingerprint covers project metadata, scoped records, indexed history, linked-session metadata, and Git observations. Read timestamps are excluded. Unchanged history digests are reused to avoid rereading long transcripts on each project opening. Repository inspection and changed-file hashing are bounded; large repositories may return limited coverage or an explicit read error.

Each successful existing brief refresh saves its baseline in the current brief and its revision snapshot. Failed refreshes preserve the last successful brief and baseline. Old briefs remain accessible; refreshing establishes their first baseline. If Git cannot be inspected or changed-file detection reaches its safety limit, freshness is unknown rather than current.

The baseline is captured before synthesis, so changes during generation can make the resulting brief stale. “Fresh” means no detected evidence changes, not that the AI claims have been independently verified.

## Project status

On the Dashboard, click **Refresh project status** to generate an updated summary using the existing Codex integration. The Dashboard leads with current status, blockers, and suggested next steps; expand the other sections for details. Numbered citations open the matching passage in Sources.

New summaries distinguish verified observations, reported claims, inferences, and unresolved issues. Only exact observations collected by ProjectHub can retain a verified label. The validation section states when no independent test results are available; this does not imply that tests were never run.

The generator reads linked history in bounded batches and reuses cached batch summaries when possible. Long histories can take longer and the final summary can omit details. Previous summaries are comparison context, not evidence. Existing versions remain readable in **Previous versions**. Failed generation keeps the last successful version.
