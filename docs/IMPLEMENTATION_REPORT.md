# Implementation status

ProjectHub is a local FastAPI/SQLite and React application installed from a source checkout.

Implemented workflows include project metadata, notes, TODO editing/deletion and atomic bulk capture, imported ChatGPT share snapshots, linked Codex history, local indexed search, cited AI briefs and decision Q&A, saved generation versions, and read-only project discussions.

AI writing instructions live in `AI_RECOMMENDATIONS.md`. Generated responses and imported text remain untrusted reference material. Useful review responses can be explicitly saved as proposed notes. Existing decisions and audit snapshots are preserved.

Setup installs Python dependencies into `.venv` and builds the frontend. The user-local launcher links to the checkout. Configuration and mutable project data belong in XDG directories, outside source control. Codex credentials remain managed by Codex.

At the original handoff, installation required a source checkout. See the current
[installation guide](GETTING_STARTED.md) and [security boundaries](SECURITY_REVIEW.md).
