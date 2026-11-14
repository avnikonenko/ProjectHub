# ProjectHub

<img src="frontend/src/projecthub-logo.png" alt="ProjectHub stacked-layers logo" width="480" />

A local workspace for understanding and continuing projects across Codex sessions and shared ChatGPT conversations. Search saved history, keep Markdown notes and TODOs, and generate project overviews, Q&A, and diagrams with source references.

![ProjectHub dashboard with an example brief and numbered source references](docs/images/projecthub-dashboard.png)

*Example data; no personal conversations or credentials are shown.*

## Install and run

Requires Linux, Python 3.11+ with venv support, Git, and Node.js 22.18+ with npm. From a downloaded or cloned checkout:

```bash
bash scripts/install.sh
projecthub
```

Open **http://127.0.0.1:8766**. Stop with Ctrl+C. If the command is outside your PATH, use `bash scripts/projecthub`. Keep the checkout: the installed command links to it. Standalone Python wheels are not supported.

## Configuration and sign-in

Run `projecthub config` to create or locate `~/.config/projecthub/config.toml`. See [config.example.toml](config.example.toml). Restart after changes. Environment variables override the file; `PROJECTHUB_CONFIG` selects another file.

For AI features, install Codex separately and run `codex login` with your ChatGPT account. ProjectHub uses Codex's existing credentials and requires no OpenAI API key. **Do not put credentials in this repository.**

To add an existing profile, open **Settings → Add Codex profile** and enter its name and Codex directory, such as `~/.codex`. Select it in the project's AI settings; existing sessions keep their original profile. The configuration's `ai.codex_home` can also select an existing profile.

Project data defaults to `~/.local/share/projecthub`, outside the checkout. Back up that directory. Requested AI work sends prompts and selected project context to OpenAI. Browsing, search, history indexing, and freshness checks run locally without model requests.

**Single-user, local-only application:** do not expose it publicly without authentication and a security review.

## Use

Open a project to load its workspace. The top search field searches saved project information with filters by record type; results and conversation history are paginated.

- **Sources / History:** import public ChatGPT share links, link Codex sessions, and browse saved conversations. Unlinking stops active history sync while retaining records; link the same thread ID to restore it. Shared fork prefixes collapse without removing original messages. Select a passage to save a linked note.
- **Overview / AI Q&A:** generate a concise project overview and explanations from conversations and bounded repository evidence. Local checks flag outdated overviews. See [Project Overview and Knowledge](docs/PROJECT_KNOWLEDGE_UI.md) and [evidence limits](docs/PROJECT_EVIDENCE.md).
- **Knowledge:** search extracted choices, requirements, and supporting evidence. Review saved generation previews and activate matching Knowledge and Overview with **Use this result**, without another AI call. Failed generation preserves saved results; previews survive restart and export as JSON.
- **Scheme:** generate architecture and workflow diagrams, inspect dependencies, and browse code structure. AI runs only on explicit refresh. See [Scheme](docs/SCHEME.md).
- **Git:** link a repository in **Edit overview** for branch, commit, tracked-change, and untracked-file status. Inspection is read-only; it does not run tests or inspect changes inside submodules. Expand **Open GitHub issues** for public repository issues.
- **TODO / Notes:** capture and bulk-add follow-ups; keep longer Markdown notes.
- **Reviews:** ask cited questions about saved project information or ask a Codex session, then save useful responses as proposed notes.

Use **AI settings → Project AI instructions** for project-specific response preferences. The editor starts with [shared recommendations](AI_RECOMMENDATIONS.md); edit, remove, or clear them. Read-only and evidence rules still apply. Older structured question/decision records remain historical references; there is no separate acceptance workflow.

See [AI generation](docs/AI_GENERATION.md) for progress, cancellation, time budgets, caching, and quality profiles. Quality is the default; Balanced and Economy are experimental, and Economy can omit source windows. **More parallel-reading threads can exhaust local resources or hit account limits.** Review coverage and reported usage in Generation details.

## Development

```bash
projecthub check
# Or: bash scripts/check.sh
```

Test files and conversation claims do not establish passing tests or scientific validity. Follow the [validation guide](docs/TEST_REPORT.md) for release checks.

[Documentation](docs/README.md) · [Architecture](docs/ARCHITECTURE.md) · [Security](docs/SECURITY_REVIEW.md) · [Changelog](CHANGELOG.md) · [MIT license](LICENSE)
