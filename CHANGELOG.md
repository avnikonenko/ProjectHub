# Changelog

Add user-facing changes under Unreleased. Move them into a dated version section when cutting a release.

## Unreleased

### Added

- Delete notes from the Notes tab without deleting original files or conversations.
- Unlink Codex sessions from Sources without deleting their original threads or saved ProjectHub records.

- Save selected passages from conversation history as notes, with an optional comment and a link to the source.
- Ask Project in Reviews answers from saved project evidence and links to its sources.
- The dashboard shows the branch, three recent commits, and working-tree status for linked Git repositories.
- Linked Codex history syncs in the background. Individual sources can be paused without losing saved history.

### Changed

- Sources supports renaming, searching, and filtering sessions, with the most recent local activity shown first.
- Reviews keeps continuing Codex discussions collapsed by default. New review sessions take their names from the first question.
- The stacked-layers logo appears in the README, with a transparent icon in the browser tab and sidebar.
- Removed structured decision acceptance prompts from the interface while preserving historical records and audit history.

### Maintenance

- Split self-contained frontend components out of the main application file without changing user-facing behavior.
