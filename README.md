# ProjectHub

<img src="frontend/src/projecthub-logo.png" alt="ProjectHub logo" width="240" />

A local workspace that helps you return to a project without reconstructing its history. ProjectHub brings together Codex sessions, shared ChatGPT conversations, repository evidence, notes, and follow-ups to explain what the project does, what changed, and what to work on next.

## Features

- **Project overview:** generate a concise current-state analysis with source references, freshness indicators, and previous versions.
- **Conversations:** link terminal Codex sessions, import public ChatGPT share links, and browse saved history. Shared history from forked sessions collapses without deleting messages.
- **Search:** find conversations, notes, TODOs, and generated information locally, with filters and paginated results.
- **Knowledge and AI Q&A:** extract requirements, choices, and supporting evidence; generate project-specific answers and review saved results before activating them.
- **Scheme:** visualize architecture and workflows, inspect dependencies, and browse repository structure.
- **Notes and TODOs:** write Markdown notes, save conversation passages, bulk-add tasks, and pin important follow-ups.
- **Git:** inspect branches, recent commits, tracked changes, untracked files, and public GitHub issues without modifying the repository.
- **Reviews and continuity:** ask questions about the project, save useful responses, and preview context before sending it to a Codex session.
- **Customization:** choose Codex profiles, models, reasoning effort, and project AI instructions; adjust colors and text size.

![ProjectHub dashboard with example data](docs/images/projecthub-dashboard.png)

## Install and run

Requires **Linux, Python 3.11+ with venv, Git, and Node.js 22.18+ with npm**. From a cloned or downloaded checkout:

```bash
bash scripts/install.sh
projecthub
```

Open **http://127.0.0.1:8766**. Stop with Ctrl+C. If the command is outside your PATH, run `bash scripts/projecthub`. Keep the checkout: the launcher links to it.

## Settings and AI

Run `projecthub config` to locate your editable configuration. See [config.example.toml](config.example.toml). Environment variables override file settings; restart after changes.

AI features use your existing **Codex ChatGPT sign-in**: install Codex separately and run `codex login`. No OpenAI API key is required. Choose an existing profile, model, and reasoning effort in Settings; edit project AI instructions there too.

Browsing and search stay local. Requested AI generation sends selected project context to OpenAI. Generated claims need review; source references do not establish correctness. Higher parallel-reading settings can hit account or memory limits. See [AI generation and limits](docs/AI_GENERATION.md).

## Data and safety

Settings default to `~/.config/projecthub/config.toml`; project data to `~/.local/share/projecthub`. **Back up the data directory and keep credentials out of Git.**

Single-user, local-only application. Do not expose it publicly without authentication and a security review.

## Development

Run `projecthub check` or `bash scripts/check.sh` for tests and the frontend build.

[Documentation](docs/README.md) · [Validation](docs/TEST_REPORT.md) · [Security](docs/SECURITY_REVIEW.md) · [Changelog](CHANGELOG.md) · [MIT license](LICENSE)
