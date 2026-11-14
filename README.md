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

Startup loads only project names and IDs. Open a project to load its workspace. Saved Codex history shows three turns per page; page navigation replaces the previous page in the browser. Background history sync indexes linked sessions on disk without sending a model request.

Use the single search field at the top to search saved project information. Filter results by TODOs, Notes, Conversations, AI Q&A, Knowledge or Sessions. Results load in bounded pages; searching does not invoke AI or refresh source history.

- **Sources:** import public ChatGPT share links and manage Codex sessions. Unlink removes a session from active history and sync while keeping its records. Add the same thread ID again to relink it.
- **History:** browse and search saved conversations. Exact inherited prefixes from forked sessions collapse into expandable shared history after indexing. Original messages remain available. Select text to save it as a note with a link back to the source.
- **Scheme:** generate evidence-linked architecture and workflow diagrams, inspect direct dependencies, and browse code structure. It uses existing project AI settings and runs AI only on explicit refresh. See [Scheme](docs/SCHEME.md).
- **Overview / AI Q&A:** generate a compact overview and explanations of choices from conversations. Project status also reads bounded code and documentation excerpts during explicit refreshes and reconstructs architecture from those excerpts and sessions when no diagram is saved.
- **Knowledge:** search extracted choices, requirements, reasons, and original evidence. Matching knowledge-generated overviews link to important choices. Saved generated results survive refresh/restart and can be downloaded as JSON. Use this result activates matching Knowledge and Overview without another AI call; failed generation or activation preserves saved results. Browsing is read-only; generation remains opt-in. See [Project Knowledge](docs/PROJECT_KNOWLEDGE_UI.md).
- Link a Git repository in **Edit overview** to see its current branch, recent commits, and top-level working-tree status on the dashboard. This is a local read-only snapshot; it does not run tests or inspect changes inside submodules. Expand **Open GitHub issues** to see up to three issues from a public GitHub repository. You can also set its URL in **Git remote**.
- **TODO / Notes:** capture follow-ups, bulk-add TODOs, and keep longer notes. Notes render Markdown headings, lists, emphasis, code blocks, and links. Use the trash icon to remove a note from ProjectHub.
- **Reviews:** use Ask Project for cited answers from saved conversations and notes, or ask a Codex session and save useful responses as proposed notes.

Edit [AI_RECOMMENDATIONS.md](AI_RECOMMENDATIONS.md) to adjust shared AI writing instructions.

There is no separate question/answer acceptance workflow. Older structured records remain available in exports and as historical reference.

The dashboard checks brief freshness locally without calling AI. A successful refresh saves an evidence baseline; later Git, history, note, or TODO changes can mark it outdated. See [project evidence](docs/PROJECT_EVIDENCE.md) for limits and exclusions.

**Codex profiles:** In the top-bar **Settings → Add Codex profile**, enter a name and an existing Codex directory (for example, `~/.codex`). ProjectHub checks the existing ChatGPT login, saves only the name and path in its local database, and avoids duplicate directories. Select the profile afterward to use it for this project. Existing sessions keep their original profile.

**Project AI instructions:** Open **AI settings → Project AI instructions** to set wording, language, or other response preferences for this project. These apply to future Project status, AI Q&A, Ask Project, and Codex review requests sent through ProjectHub. The editor starts with the shared recommendations. Edit or remove any of them, or save it blank to use none. Read-only and evidence rules still apply.

**Validation:** Test files and conversation claims do not establish that tests passed. Look for recorded test results.

For progress, cancellation, history sync, caching and parallel-reading settings, see
[AI generation](docs/AI_GENERATION.md). Higher thread counts can exhaust local resources
or encounter account limits; read the [parallel-reading warning](docs/AI_GENERATION.md#parallel-reading).
For opt-in knowledge generation, **Settings → Knowledge generation profile** offers
Quality (default), Balanced and Economy (experimental). New previews show reported
tokens, request/cache counts and coverage under **Generation details**. Economy can
omit source windows; review its partial-coverage warning. See
[profiles and usage](docs/AI_GENERATION.md#quality-and-usage-profiles).

## Development

```bash
projecthub check
# Or: bash scripts/check.sh
```

[Documentation](docs/README.md) · [Validation guide](docs/TEST_REPORT.md) · [Architecture](docs/ARCHITECTURE.md) · [Security](docs/SECURITY_REVIEW.md) · [Changelog](CHANGELOG.md)

Historical methods, measurements and results are kept separately under [Experiments](docs/experiments/README.md).

Single-user, local-only application. Do not expose it publicly without authentication and a security review. Installation is supported from a source checkout; standalone Python wheels are not currently a supported distribution.

[MIT license](LICENSE).
