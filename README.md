# ProjectHub

<img src="frontend/src/projecthub-logo.png" alt="ProjectHub stacked-layers logo" width="480" />

A local workspace for project history, decisions, notes, and TODOs. Import shared ChatGPT conversations, link Codex sessions, search saved history, and generate concise project briefs and Q&A with source references.

![ProjectHub dashboard in dark mode, showing an example project brief with numbered source references](docs/images/projecthub-dashboard.png)

*Example data; no personal conversations or credentials are shown.*

## Install and run

Requires Linux, Python 3.11+ with venv support, Git, and Node.js 22.18+ with npm.

From a downloaded or cloned source checkout:

```bash
bash scripts/install.sh
projecthub
```

Open **http://127.0.0.1:8766**. Stop with Ctrl+C. If the command is outside your PATH, use `bash scripts/projecthub`. Keep the checkout: the installed command links to it.

## Configuration and sign-in

Run `projecthub config` to create or locate your private editable TOML file at `~/.config/projecthub/config.toml`. See [config.example.toml](config.example.toml). Restart after changing settings. Environment variables override the file; `PROJECTHUB_CONFIG` selects another file.

For AI features, install Codex separately and run `codex login` with your ChatGPT account. Existing sign-ins stay in Codex's own credential storage. Set `ai.codex_home` to use another existing Codex profile, or select a profile in the project's gear menu. ProjectHub does not require or accept an OpenAI API key. **Do not put credentials in this repository.**

Project data defaults to `~/.local/share/projecthub` and stays outside the checkout. Back up that directory. Prompts and selected project context go to OpenAI when you request AI work.

## Use

- **Sources:** import public ChatGPT share links and manage Codex sessions. Unlink removes a session from active history and sync while keeping its records. Add the same thread ID again to relink it.
- **History:** browse and search saved conversations. Select text to save it as a note with a link back to the source.
- **Dashboard / AI Q&A:** generate a compact overview and explanations of choices from conversations.
- Link a Git repository in **Edit overview** to see its current branch, recent commits, and top-level working-tree status on the dashboard. This is a local read-only snapshot; it does not run tests or inspect changes inside submodules.
- **TODO / Notes:** capture follow-ups, bulk-add TODOs, and keep longer notes. Use the trash icon to remove a note from ProjectHub.
- **Reviews:** use Ask Project for cited answers from saved conversations and notes, or ask a Codex session and save useful responses as proposed notes.

Edit [AGENTS_AI_agent.md](AGENTS_AI_agent.md) to adjust shared AI writing instructions.

There is no separate question/answer acceptance workflow. Older structured records remain available in exports and as historical reference.

Saved Codex history syncs locally in the background about once a minute while ProjectHub runs. Unchanged sessions are skipped, and previous search data remains usable if a read fails. Use **Pause automatic sync** in Sources for an unavailable session; its saved history and requests remain intact. Manual refresh is available in History. Sync never starts AI generation; imported ChatGPT share links remain snapshots. To disable sync or change the interval, add `[history]` with `auto_sync = false` or `sync_interval = 120` to the config.

## Development

```bash
projecthub check
# Or: bash scripts/check.sh
```

[Changelog](CHANGELOG.md) · [Validation](docs/TEST_REPORT.md) · [Architecture](docs/ARCHITECTURE.md) · [Security](docs/SECURITY_REVIEW.md)

Single-user, local-only application. Do not expose it publicly without authentication and a security review. Installation is supported from a source checkout; standalone Python wheels are not currently a supported distribution.

[MIT license](LICENSE).
