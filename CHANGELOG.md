# Changelog

Add user-facing changes under Unreleased. Move them into a dated version section when cutting a release.

## Unreleased

### Added

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

- Latest activity includes the linked repository’s three most recent Git commits in date order with project activity.

- AI generation shows plain-language stages and elapsed time across project tabs.

- Renamed the dashboard’s main summary to Project status; Dashboard remains the page name.

- Sources shows session names, IDs, and Codex activity timestamps when available. Edit a session to assign a topic, then organize Sources by topic or recent activity.

- Sources supports renaming, searching, and filtering sessions, with the most recent local activity shown first.
- Reviews keeps continuing Codex discussions collapsed by default. New review sessions take their names from the first question.
- The stacked-layers logo appears in the README, with a transparent icon in the browser tab and sidebar.
- Removed structured decision acceptance prompts from the interface while preserving historical records and audit history.

### Fixed

- Project status and AI Q&A progress stay visible when switching tabs in the same project.
- Brief generation reports supported Codex failure reasons and ignores completion events from other turns.

- Older project summaries show their overview first, and sources for discarded validation entries are no longer saved.

- Project status freshness now detects Git staging and edits to historical accepted decisions. Deleted files no longer make freshness permanently unknown.
- Editing project details or the linked repository rechecks dashboard freshness.
- Project goals and description now reach the project-status generator instead of being omitted from its evidence.

### Maintenance

- Extracted brief rendering and source-reference components while preserving the existing dashboard and revision history.

- Split self-contained frontend components out of the main application file without changing user-facing behavior.
