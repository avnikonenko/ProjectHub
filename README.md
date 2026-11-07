# ProjectHub

A local workspace for project history, decisions, notes, and TODOs. Import shared ChatGPT conversations, link Codex sessions, search saved history, and generate concise project briefs and Q&A with source references.

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

- **Sources:** import public ChatGPT share links and manage Codex sessions.
- **History:** browse and search saved conversations.
- **Dashboard / AI Q&A:** generate a compact overview or decision Q&A.
- **TODO / Notes:** capture follow-ups, bulk-add TODOs, and keep longer notes.
- **Reviews:** ask project questions; save useful answers as proposed notes.

Edit [AGENTS_AI_agent.md](AGENTS_AI_agent.md) to adjust shared AI writing instructions.

## Development

```bash
projecthub check
# Or: bash scripts/check.sh
```

[Validation](docs/TEST_REPORT.md) · [Architecture](docs/ARCHITECTURE.md) · [Security](docs/SECURITY_REVIEW.md)

Single-user, local-only application. Do not expose it publicly without authentication and a security review. Installation is supported from a source checkout; standalone Python wheels are not currently a supported distribution.

[MIT license](LICENSE).
