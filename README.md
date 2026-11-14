# ProjectHub

<img src="frontend/src/projecthub-logo.png" alt="ProjectHub logo" width="240" />

A local workspace for understanding and continuing projects across Codex sessions and shared ChatGPT conversations.

- Search saved conversations and keep Markdown notes and TODOs.
- Generate cited project overviews, Q&A, architecture, and workflow diagrams.
- Inspect read-only Git status and resume work with relevant project context.

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
