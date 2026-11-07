# ProjectHub

ProjectHub helps you catch up on projects, revisit decisions, keep notes, and track next steps. It stores project records in a local SQLite database. The browser UI and API run as one loopback-only service. Core project management works without Codex or an API key.

**Validation status:** implementation and test status are recorded in [docs/TEST_REPORT.md](docs/TEST_REPORT.md). Do not infer successful installation or real AI generation from mocked protocol tests.

## Ubuntu setup

Requirements: Python 3.11+, Python venv support, Git, Node.js 22.18+ with npm, and network access to the Python and npm registries during installation. Codex CLI is optional for core use. No Docker or external database is needed.

From this checkout:

```bash
bash scripts/setup.sh
bash scripts/check.sh
bash scripts/start.sh
```

Open **http://127.0.0.1:8766**. Stop with Ctrl+C. The startup command serves the built React application and FastAPI from a single process. `PROJECTHUB_PORT` selects a different loopback port. The scripts recognize the existing Miniforge Node installation on this computer when Node is absent from PATH.

To install a convenient user-local command after setup:

```bash
bash scripts/install-local.sh
projecthub
```

The installer creates `~/.local/bin/projecthub` (or `$XDG_BIN_HOME/projecthub`) and does not copy application data into the source tree. Use `projecthub help` to see the `start`, `check`, and `setup` commands.

For frontend development, start the backend using the startup script and, in a second terminal, run `npm run dev` in `frontend/`. Vite proxies `/api` to the backend. Rebuild with `npm run build` before using the single-service startup again.

## First project

Terminal agents can add notes through the running local app. Use `projecthub projects` to find a project ID, then `projecthub note add --project-id 1 --title 'Finding' --file /path/to/note.md`. `--content` and `--stdin` are also supported. These appear in Notes as **AI note** with the originating `CODEX_THREAD_ID` when available. This records a note without accepting a decision or calling a model. The reusable instructions are in [skills/projecthub-notes/SKILL.md](skills/projecthub-notes/SKILL.md).

Create a project with its objective and goals. An optional repository path must be the absolute root of an existing Git repository. Registration inspects the path and Git metadata without moving or changing repository files. Symlink paths and traversal components are rejected.

Use TODO to add and check off follow-ups. Use AI Q&A to revisit decisions and the reasons behind them. Use Notes for Markdown text, assumptions, evidence, limitations and UTF-8 `.md`/`.txt` imports up to 2 MiB. Each project has its own search, summary, review and export.

## Data and backups

Default mutable storage is `$XDG_DATA_HOME/projecthub`, or `~/.local/share/projecthub` if XDG_DATA_HOME is unset. The database is `projecthub.sqlite3`; imported documents and consistent backups live beneath the same application directory. Set `PROJECTHUB_DATA_DIR` to an absolute private directory to relocate all application data. No mutable database is stored in the checkout by default.

Use the project JSON export for portable project records and provenance. `POST /api/backup` creates a consistent SQLite backup in the managed data directory. For disaster recovery, stop ProjectHub and copy the complete data directory to safe storage. Restore while the application is stopped; retain a copy of the previous directory first. JSON import/restore is deferred.

## Codex

Use the gear menu to choose an existing Codex account/profile for each project. The choice is saved in ProjectHub and applies to model listing and future AI requests. Profiles are discovered from the default Codex home and local Codex Manager profile directories. ProjectHub launches the official client with that profile's home; it does not copy credentials or change your global sign-in. Existing history is read from its original profile. To continue an existing discussion, select its original profile or start a new session under the chosen profile.

Use **History** to browse saved turns from any Codex session associated with the project. Build or refresh the search index to search finished turns across all linked sessions and open the exact matching turn. Indexing stores a searchable copy of saved user and assistant text in the local ProjectHub database and does not send it to a model. Refresh the index to include newer turns.

On the dashboard, **Generate brief** processes saved user and assistant text from every linked session in batches, alongside project notes (including AI notes), accepted decisions and your saved architecture diagram. AI Q&A also uses project notes. AI-authored notes remain unverified reference material. Generation uses OpenAI through the selected Codex sign-in, skips turns still in progress, and leaves out tool output and attachments. A large first refresh can take several minutes; later refreshes reuse unchanged batches. During generation, stage and session/turn/batch counts show progress.

Saved briefs and Q&A show the account profile, model and reasoning effort used for that snapshot. When Codex does not report an effective default, it is labeled as a default instead of guessing. Older saved results show that generation settings were not recorded.

The saved brief cites original source fragments and separates possible decisions from accepted ones. The Dashboard includes a short architecture section when the sources support it. Saved Mermaid diagrams remain available as brief sources. Refreshing is manual; a failed refresh keeps the previous brief.

ProjectHub uses the installed official `codex app-server` over a private stdio connection. It does not read authentication files, modify global Codex configuration, or edit internal conversation history. Check the CLI separately with:

```bash
codex --version
codex login status
```

Sign in using Codex's official login flow outside ProjectHub if needed. ProjectHub requires ChatGPT-backed authentication for its integration rather than silently using a separately billed API-key provider. Account access and subscription limits still apply. The integration was developed against locally generated protocol schemas from Codex 0.166.0; incompatible versions report errors rather than simulate supported sessions. Official protocol reference: [Codex App Server](https://developers.openai.com/codex/app-server).

Codex operations are read-only in this MVP. Create an app-owned discussion session, or link an existing thread to read its saved user/assistant history without resuming it. Resuming a linked thread is a separate explicit action and may fail while another Codex process owns it. Review context and decision updates before sending. Accepting an answer never sends anything automatically. Delivery states distinguish local drafts/queues, submitted turns, completion and failure. Busy sessions require explicit retry; a queued database row is not proof of delivery. Imported text and AI output are untrusted proposals. Saving a response must not confer acceptance.

## Architecture

React/TypeScript/Vite → same-origin FastAPI → SQLAlchemy/SQLite with Alembic migrations and FTS5. Question acceptance and reopening use audited transactions. A separate integration module manages the Codex subprocess, protocol responses, events and context snapshots.

See [architecture](docs/ARCHITECTURE.md), [data model](docs/DATA_MODEL.md), [API contract](docs/API_CONTRACT.md), [implementation plan](docs/IMPLEMENTATION_PLAN.md), and [security review](docs/SECURITY_REVIEW.md).

## Troubleshooting and limits

- If pip/npm cannot reach their registries, installation cannot complete. Check network availability and rerun setup; the scripts stop on errors.
- If the UI build is missing, rerun setup or build `frontend/`; startup reports the missing artifact.
- If Codex is missing, unsigned-in, incompatible or interrupted, keep using core project management and inspect the error in Codex views. ProjectHub does not bypass sandbox restrictions.
- If the default data directory is unwritable, set `PROJECTHUB_DATA_DIR` to a writable private directory before startup.
- This is a single-user local application. Do not expose it through a network bind or reverse proxy without adding authentication and conducting a separate security review.
- Rich PDF/Word import, semantic/vector search, automatic dependency discovery, code-editing permissions, multi-user collaboration and automatic decisions are deferred. Rule-based reviews flag gaps in records and cannot verify claims.
- Python requirements use bounded version ranges; installation should be validated in the target environment. The frontend lockfile, when present, pins its dependency graph.
