# Project evidence and brief freshness

ProjectHub collects evidence locally when the dashboard checks brief freshness. This does not initialize Codex, generate AI text, execute project code, or run tests.

## Evidence collected

- User-supplied objective, project metadata, notes, and TODOs.
- Saved, indexed turns from currently linked Codex sessions, plus imported conversation notes.
- Git branch, HEAD, recent commit metadata, visible modified/untracked paths, and relevant filenames for documentation, tests, and dependency manifests.
- Bounded hashes of visible changed files for detecting another edit to the same path. File contents are not included in these automatic freshness checks.

Git inspection uses argument arrays, read-only settings, output/time limits, and disables configured filters and hooks. It supports Git worktrees. Git-ignored paths, submodule contents, generated directories, and common private-file patterns are omitted. No remote fetch or project test command is run.

Set PROJECTHUB_EVIDENCE_EXCLUDE before startup to a JSON array of additional glob patterns, for example:

    export PROJECTHUB_EVIDENCE_EXCLUDE='["private/**", "data/**"]'
    projecthub

This supplements built-in exclusions. It applies to repository evidence, not notes or conversations explicitly saved in ProjectHub.

## What the status means

The overview distinguishes repository observations from conversation claims, plans, and AI interpretations. ProjectHub does not run tests during inspection or independently collect test results.

## Freshness

A stable fingerprint covers project metadata, scoped records, indexed history, linked-session metadata, and Git observations. Read timestamps are excluded. Unchanged history digests are reused to avoid rereading long transcripts on each project opening. Repository inspection and changed-file hashing are bounded; large repositories may return limited coverage or an explicit read error.

Each successful existing brief refresh saves its baseline in the current brief and its revision snapshot. Failed refreshes preserve the last successful brief and baseline. Old briefs remain accessible; refreshing establishes their first baseline. If Git cannot be inspected or changed-file detection reaches its safety limit, freshness is unknown rather than current.

The baseline is captured before synthesis, so changes during generation can make the resulting brief stale. “Fresh” means no detected evidence changes, not that the AI claims have been independently verified.

## Project status

On the Dashboard, click **Refresh project status** to generate an updated summary using the existing Codex integration. The Dashboard leads with current status, blockers, and suggested next steps; expand the other sections for details. Numbered citations open the matching passage in Sources.

Summaries label repository observations, reported claims, inferences, and unresolved issues separately. Missing test results mean none were recorded by ProjectHub, rather than that no one ran tests.

The generator reads linked history in bounded batches and reuses cached batch summaries when possible. Long histories can take longer and the final summary can omit details. Previous summaries are comparison context, not evidence. Existing versions remain readable in **Previous versions**. Failed generation keeps the last successful version.

An explicit Project status refresh also supplies bounded source-code and documentation excerpts to Codex. Repository text is untrusted data and is never executed. Architecture can be reconstructed from those excerpts and session evidence; inferred connections remain labeled. Excerpt coverage is recorded with the saved summary.

## Shared history in forked sessions

ProjectHub detects exact initial sequences shared by linked sessions in the same project.
A match requires at least two nonblank finished turns and 200 text characters.
Message roles, text, order, and terminal status must match; content after a divergence
remains separate, even when individual later messages repeat.

Original Codex files and saved per-session index records remain unchanged. History
pages can collapse confirmed shared turns from complete saved indexes; unindexed or
incomplete histories remain visible normally. The first check after an index change
streams saved text to compute fingerprints; later pages reuse bounded cached metadata.

AI history preparation stages text in temporary files, then summarizes retained turns
once. Alternate session references are attached after summary-cache lookup, so adding
a fork does not invalidate unchanged canonical summaries. Ask Project, Scheme, and
recent overview excerpts also deduplicate confirmed indexed prefixes. Previous saved
reports are not rewritten. Search still exposes original per-session matches.

Deduplication compares the normalized user/assistant text available to ProjectHub.
It does not establish equality of tool output or attachments, which are excluded
from AI history evidence. Original session copies and their references remain intact.
Different retrieved excerpts from the same shared turn remain separate evidence;
only identical excerpt text is collapsed. Known-stale indexes are excluded from sharing decisions, and sharing stops at
unreadable rows. Collapsed groups preserve visible turn order.
