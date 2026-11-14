# Changelog

Add user-facing changes under Unreleased. Move them into a dated version section when cutting a release.

## Unreleased

### Fixed

- Explain oversized Knowledge overview inputs with the actual compacted character count, limit, overage and included item/code counts; distinguish size limits from timeouts and name the preserved saved results.
- Apply the shared parallel AI worker setting to legacy Project status and AI Q&A history processing and independent evidence-reduction groups. Capture settings per job, preserve source order and existing caches, retain completed work on failure, and avoid unnecessary reduction calls when the tail already fits.
- Preserve complete Scheme workflow passages and diagrams instead of cutting notes at a fixed prefix. Retrieve relevant later sections with exact spans and speaker context, keep fenced diagrams intact, and deduplicate shared turns before applying the existing evidence budget. Distinguish proposed sequence from unknown validation criteria or implementation.
- Match thread-count and generation-profile help text to the other Settings explanations, using the same font size and theme colour.
- Make Knowledge reload explicit: reread saved published results and the saved preview, show loading/success/error feedback, and open the saved preview when no published snapshot exists. Preserve saved results on read failures and keep reload separate from generation/publication.
- Clear heading-specific context warnings when fresh extraction supplies the missing continuation, while preserving independent conflicts. Keep incomplete-heading warnings when the continuation exceeds the bounded context size.
- Reopen and focus a knowledge record on every relationship-link selection, including an already-selected record that was manually collapsed.
- Preserve the qualifying continuation when an extraction window ends at an assistant heading. Reuse unaffected caches; flag older heading-only interpretations for context review without rewriting their status or evidence.
- Keep knowledge briefs focused on project meaning instead of repeating citation-processing details.
- Reduce repeated preference cards through expandable interpretations from the same user passage, preserving every record, filter result and direct link.
- Preserve independent tentative proposals and clearly requested preferences during extraction. Treat tool tables repeated as question context conservatively rather than adopting every listed backend; version the extraction instructions and use smaller context-preserving windows.
- Generate briefs around project workflow, integrity/retention policies and reasons, keeping design agreement separate from implementation evidence and avoiding repeated evidence-label prefixes.
- Keep lossless compaction at 180,000 characters and bound synthesis to 240,000 so the evaluated detailed knowledge fits without dropping records. Cached extraction remains reusable.
- Fit larger knowledge previews using explicit defaults for repeated item fields in the synthesis request. Preserve every record, status, rationale and quotation, with the synthesis size limit unchanged.
- Explain malformed knowledge-extraction responses precisely, including the returned item count when it exceeds the bound, while preserving successful batch caches and withholding source content from errors.
- Keep words intact in Markdown table cells so short headers such as Priority do not split across lines; wide tables scroll locally.
- Restore named imported ChatGPT conversations in Sources with paged links to saved transcripts and original shared chats.
- Count successfully cached extraction batches in failed-run progress even when a sibling fails before the coordinator records their completion. Preserve the original failure and stop further dispatch.
- Require source support for extracted reasons and limitations as well as statements. Knowledge briefs cross-check original excerpts instead of repeating unsupported caveats or interpreting recommendations and missing metadata as proof that work or version pinning is absent.
- Preserve configurable policies and concrete inspection properties in knowledge statements. After real comparisons exposed mixed-status compression at 24 records, increase the bounded extraction ceiling to 36 and version the instructions/schema; retain earlier caches without reusing obsolete interpretations.
- Evaluation distinguishes statement-level knowledge, structured details and quotation-only retention, without treating regex matches as semantic-accuracy scores.

### Added

- An editable Overall time budget in Settings (30–7200 seconds, default 900), saved locally and applied to the next generation without changing active requests.
- Activate a reviewed saved Knowledge result with **Use this result**, updating its matching Overview atomically without another AI call. Reject changed sources, repository evidence or Overview baselines and preserve existing results on failure.
- Add persistent Quality, Balanced and Economy profiles for opt-in knowledge generation. Keep Quality and its matching caches unchanged; mark alternate profiles experimental, preserve explicit model/effort choices, and disclose Economy's bounded partial coverage.
- Show per-run profile, duration, requests, extraction-cache reuse, coverage and actual Codex-reported tokens in expandable preview details and JSON exports. Missing usage remains unavailable; repeated events are deduplicated and partial reports are labelled.
- Recover successful unpublished knowledge previews after refresh/restart through project-scoped revision storage. Restore active progress, preserve the previous preview after failure, and download its complete JSON without invoking AI or publishing it.
- Long notes show compact Markdown previews with Expand/Show less; searching reveals full matching notes.

- Search all Notes and TODOs before pagination, including completed TODOs, with match counts and local filtering.

- Agents can cross out existing TODOs through the ProjectHub skill using `projecthub todo complete` without deleting notes or pins.

- Main Settings includes a persistent Parallel extraction workers control (1–4), with loading/saving feedback and failed-save recovery. Changes apply to the next generation without restarting or interrupting an active run. Saving preserves unrelated ProjectHub settings and does not invoke Codex.

### Changed

- Tighten page/control spacing, keep prose at a comfortable reading width while tables and diagrams use more space, and standardize saving/progress/error/cancellation feedback near the related actions.
- Make list action icons quieter at rest, with consistent square targets and matching hover/keyboard-focus highlights. Pinned TODOs stay prominent; touch targets remain larger.
- Merge colour-palette and text-size help into one About appearance section in Settings.
- Show saved Knowledge results directly with Generate/Regenerate actions. Remove manual reload controls and preview-only explanatory panels; refresh saved data on returning to the page and refresh Overview after activation.
- Put main and project AI settings explanations in collapsed, keyboard-accessible help sections. Keep controls, save feedback, errors and active override notices visible.
- Separate current usage/configuration guides from historical experiment methods and results in `docs/experiments/`; preserve reports and link both collections from the README.
- Make parallel AI reading threads an editable count in Settings instead of a 1–4 dropdown. Keep the default at 4, limit actual concurrency to available batches, and document resource/usage risks.
- Use compact, left-aligned empty messages; omit the empty shared-conversation card in Sources and show a short inline message in History, preserving import actions and saved lists.
- Use one project-wide search at the top, with paged result-type filters for TODOs, notes, conversations, Q&A, knowledge and sessions. Remove duplicate search fields from individual tabs; search reads saved data without invoking AI.
- Standardize Overview, AI Q&A and Scheme result headers with saved time, available freshness information and one generation action; keep secondary Q&A and Scheme controls in expandable details.
- Multiple citations collapse into an expandable source count; each supporting source remains linked to its full evidence.

- Sources leads with compact ChatGPT/session add controls and the session list; connection diagnostics are expandable below, with all existing actions retained.

- Run opt-in knowledge extraction with four independent Codex workers by default (configurable from 1–4), preserving batch context, cache identity and reconciliation order. Keep completed caches on failure and stop sibling requests cooperatively; `PROJECTHUB_EXTRACTION_WORKERS=1` restores serial extraction. Record ordered batch timings in generation diagnostics.

- Persist extraction concurrency in ProjectHub's `[ai] extraction_workers` setting, with an environment override and a default of four workers. Existing saved preferences remain unchanged.

### Fixed

- Keep knowledge extraction's numeric output bounds in its prompt and local validator while constraining JSON structure and source IDs on the server, following an isolated successful probe of a repeatedly stalled dense batch. Resolve unique whitespace-only quotation selectors to exact original source spans; reject changed content, ambiguous matches and wrong speakers.

- Fit larger knowledge briefs through lossless evidence dictionaries, explicit repeated-metadata defaults and temporary citation aliases. Restore original IDs before resolving citations, preserve proposal–acceptance evidence, and keep the synthesis limit unchanged. Cache corrected source spans as literal original text for rollback compatibility.

### Fixed

- Reject metal-coordinating-water false positives in the configurable crystallographic-water recall screen. Stop excessive streamed JSON-formatting whitespace with an explicit generation failure while preserving saved results; never accept a partial response.

### Changed

- Compact extraction payload JSON without changing its parsed content or cache identity; measured payload reduction is 0.6%, not an established latency improvement.

### Fixed

- Compact knowledge-brief synthesis JSON and omit only empty optional presentation fields so the evaluated 167-record snapshot fits the existing input limit without discarding knowledge or quotations.

### Changed

- Compact knowledge extraction quotes and explanations while preserving acceptance context and independent claims. Balance coverage across primary messages; raise the bounded per-batch item ceiling to 24 after isolated tests showed important later constraints were omitted at 16. Version instructions/schema to invalidate old extraction caches safely.

### Fixed

- Align the knowledge-extraction response guard with schema bounds instead of rejecting valid large responses as invalid JSON. Deduplicate exact evidence references after validating every reference; distinct quotations and source identities remain preserved.

### Fixed

- Knowledge previews now expose shared batch progress, time budgets and cancellation controls. Cancellation responses no longer share mutable timing data with internal progress state.

### Changed

- Stop legacy evidence reduction as soon as the full remaining input fits, avoiding unnecessary model calls and preserving untouched evidence. Record stage durations in generation status/server logs and expose knowledge-extraction batch timing and cache reuse.

### Added

- GitHub-flavoured Markdown rendering for tables, task lists, and strikethrough, with palette-aware table styling.

- History batch totals, per-batch timing and cache reuse in AI generation progress; cooperative cancellation and a configurable overall generation budget preserve previous saved results and completed caches.

## [0.2.1] - 2026-11-10

### Added

- Project Knowledge tab with local search and filters, expandable reasoning and original evidence, source availability, conflicts and supersession. Matching knowledge-generated overviews link to compact important choices; unpublished previews stay separate and opt-in. Dashboard is labelled Overview; Scheme remains separate.

### Changed

- Notes use document-style Markdown typography with distinct headings, readable spacing, and contained code blocks.

- The ProjectHub agent skill submits UTF-8 Markdown note files and avoids wrapping whole notes in code fences, so saved notes render as formatted text.

- Version opt-in knowledge extraction instructions to preserve independently proposed options, concrete scientific checks and clear user selections. Source-pinned recall regressions supplement the existing agreement and cache tests; live quality evaluation remains separate from software checks.

### Fixed

- Missing publication identifiers no longer count as a matching brief/knowledge pair.

- Knowledge citation selection follows recorded proposal–acceptance chronology and preserves ambiguous pairs. Speaker/order corrections invalidate current evidence support without rewriting historical agreement. Shared synthesis quotations are sent once to avoid unnecessary input-limit failures.

- Opt-in knowledge briefs include original acceptance excerpts during synthesis and retain proposal–acceptance citation pairs when shortening visible references. Underlying knowledge and evidence are preserved.

## [0.2.0] - 2026-11-09

### Added

- Display the application version at the bottom of the sidebar; API and Codex client metadata use the same release version.

- Delete individual saved AI Q&A entries, review responses, and older overview revisions.
- Add multiple TODOs together, edit their notes, and delete individual TODOs.
- Knowledge-pilot previews can be evaluated without publishing. Successful extraction batches remain cached if later synthesis fails; publishing saves the knowledge snapshot and brief together.

- Opt-in evidence-based knowledge pilot with source-checked reference cases, contextual extraction caches, conservative history reconciliation, and atomic knowledge/brief publication. Existing brief generation remains the default pending evaluation.

- Exact shared prefixes in forked Codex histories can be collapsed while preserving original messages and alternate-session source links.

- Scheme diagrams support zoom, fit-to-view, and fullscreen without another AI request.

- Terminal agents can link their current Codex session to a project using the existing ProjectHub skill and `projecthub session link`.

- Scheme tab with source-backed architecture/workflow diagrams, direct dependencies and code structure, saved revisions, and separate freshness/coverage indicators. Explicit generation uses existing project AI settings.

- Black palette with neutral light surfaces and a charcoal (#171717) background in dark mode.

- Choose Green, Lavender, Warm sand, or Slate blue in app settings; the saved palette works with light and dark mode.

- Star TODOs to pin them first in their open or completed list, across pages and reloads.

- App settings now offer persistent text sizes from 100% to 200%, including labels and controls.

- Add a named Codex profile by directory in AI settings; duplicate directories are hidden.

- Preview open issues from a public GitHub repository on the Dashboard.

- Edit AI response instructions per project from AI settings.

- Dashboard Project status now summarizes current work, blockers, and next steps, with expandable details, evidence labels, and numbered citations.

- Local project-evidence snapshots and brief freshness checks, without automatic AI requests.
- Successful brief revisions now retain an evidence baseline; unreadable repository evidence is marked unknown rather than current.

- Delete notes from the Notes tab without deleting original files or conversations.
- Unlink Codex sessions from Sources without deleting their original threads or saved ProjectHub records.

- Save selected passages from conversation history as notes, with an optional comment and a link to the source.
- Ask Project in Reviews answers from saved project evidence and links to its sources.
- The dashboard shows the branch, three recent commits, and working-tree status for linked Git repositories.
- Linked Codex history syncs in the background. Individual sources can be paused without losing saved history.

### Changed

- AI Q&A focuses on conversations and terminal Codex sessions rather than treating backlog items as project knowledge.
- Settings panels close when clicking outside. Shared AI writing recommendations favor concise, complete answers while allowing context needed to understand them.
- Knowledge extraction retains proposal–acceptance context, separates tentative clauses from accepted commitments, and keeps historical agreement separate from current source availability.

- Knowledge-pilot briefs suppress presentation repetition without merging source knowledge, preserve distinct scopes, and distinguish missing acceptance evidence from nonacceptance.

- Overview, Q&A, Ask Project, and Scheme evidence avoid repeated copies of confirmed shared conversation prefixes. Detection is project-scoped and does not rewrite Codex history or existing reports.

- Compact project headers and consistent TODO, note, and session rows; full thread IDs remain expandable and copyable. Summary metadata, sources, and previous versions are grouped outside the main answer.

- Scheme prioritizes production/import-linked code, preserves useful declarations and static call names, and describes the main project workflow instead of its own generation pipeline.

- Linking sessions uses the Codex name or preview when available, with an optional custom name. Linked history turns show their session name alongside the ID.

- TODO titles use the full reading font size and grow with the accessibility text-size setting.

- Q&A retries reuse successful project-scoped drafts, and larger bounded evidence batches reduce sequential Codex calls.

- AI Q&A merges overlapping questions across conversations and ranks entries by lasting usefulness. The default limit is 25, and you can choose a different limit before refreshing. Citations are numbered from the displayed sources.

- Project overviews use two complete sentences, a single Current work section, and next steps based on explicit priorities rather than repeated AI suggestions.

- Project status leads with a concrete overview and reconstructs reported architecture from bounded repository excerpts and sessions. Recent original discussion can correct older compressed summaries; generic validation gaps are no longer duplicated.

- Register Codex profiles from app-wide Settings; project AI settings retain only profile selection.

- Load project workspaces only when opened, and cancel browser requests for history pages when leaving the view.

- Latest activity includes the linked repository’s three most recent Git commits in date order with project activity.

- AI generation shows plain-language stages and elapsed time across project tabs.

- Renamed the dashboard’s main summary to Project status; Dashboard remains the page name.

- Sources shows session names, IDs, and Codex activity timestamps when available. Edit a session to assign a topic, then organize Sources by topic or recent activity.

- Sources supports renaming, searching, and filtering sessions, with the most recent local activity shown first.
- Reviews keeps continuing Codex discussions collapsed by default. New review sessions take their names from the first question.
- The stacked-layers logo appears in the README, with a transparent icon in the browser tab and sidebar.
- Removed structured decision acceptance prompts from the interface while preserving historical records and audit history.

### Fixed

- Invalid Q&A citations receive one bounded retry; citations are checked against the evidence supplied to that batch.

- Shared-history groups preserve message order, complementary excerpts remain in AI evidence, unreadable or known-stale indexes do not cause unsafe collapse, and alternate-session metadata no longer invalidates unchanged reduction caches.

- Since your last update describes evidenced project changes directly instead of narrating discussion about the summary.

- History summaries constrain citations to their input batch. Selected-profile history reads reuse one reader per session, avoiding a Codex process restart for every page.

- Recover ongoing AI generation progress and results after a browser refresh, with the original elapsed time.

- Project status and AI Q&A progress stay visible when switching tabs in the same project.
- Brief generation reports supported Codex failure reasons and ignores completion events from other turns.

- Older project summaries show their overview first, and sources for discarded validation entries are no longer saved.

- Project status freshness now detects Git staging and edits to historical accepted decisions. Deleted files no longer make freshness permanently unknown.
- Editing project details or the linked repository rechecks dashboard freshness.
- Project goals and description now reach the project-status generator instead of being omitted from its evidence.

### Maintenance

- Added documented Example Research reference cases and live knowledge-pilot evaluations, including incremental updates and cache reuse. Formal human verification remains pending; the pilot stays opt-in.
- Preserved existing project briefs through the additive knowledge-storage migration.
- Publication setup includes an MIT license, a shorter illustrated README, installation tooling, and editable private configuration using existing Codex sign-in profiles.

- Reuse one request-scoped Codex transport across overview and Q&A batches while keeping each call in an isolated ephemeral thread.
- Small note edits preserve cached summaries of unchanged chunks and refresh their citation metadata.

- Reuse per-session indexed conversation text after checking its freshness, avoiding repeated history-page reads during AI generation.

- Extracted brief rendering and source-reference components while preserving the existing dashboard and revision history.

- Split self-contained frontend components out of the main application file without changing user-facing behavior.
