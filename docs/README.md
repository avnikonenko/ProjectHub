# Documentation

Start with the user guides for setup and everyday use. Developer references cover
implementation, tests, and deployment boundaries.

## Use and configuration

- [Install and run](../README.md#install-and-run)
- [Configuration example](../config.example.toml)
- [AI generation, settings, and troubleshooting](AI_GENERATION.md)
- [Project evidence and freshness](PROJECT_EVIDENCE.md)
- [Project Overview and Knowledge](PROJECT_KNOWLEDGE_UI.md)
- [Scheme](SCHEME.md)
- [Terminal-agent context and reading](TERMINAL_AGENTS.md)

## For contributors

Run `projecthub check` or `bash scripts/check.sh` for backend/frontend tests,
TypeScript checks, and the production build.

- [Architecture](ARCHITECTURE.md)
- [HTTP API contract](API_CONTRACT.md)
- [Data model](DATA_MODEL.md)
- [Knowledge pipeline and isolated testing](KNOWLEDGE_PIPELINE.md)
- [Knowledge interface API and implementation](KNOWLEDGE_UI_INTERNALS.md)
- [Validation commands and scope](TEST_REPORT.md)
- [Security and deployment boundaries](SECURITY_REVIEW.md)
- [Engineering decisions](DECISIONS.md)
- [Scheme implementation reference](scheme-implementation.md)

### Build a wheel

From a source checkout, use Node.js 22.18+ and npm to build the frontend, then
build the wheel with Python:

```bash
npm --prefix frontend ci
npm --prefix frontend run build
python3 -m pip wheel --no-deps --wheel-dir dist .
```

Install the resulting wheel with pipx or pip in a virtual environment. Its frontend, migrations, shared AI
instructions, and optional Codex skill are included. Use an isolated data directory
when testing a release build.

### Reload local development changes

When running the managed `projecthub` launcher, use **Settings → Restart ProjectHub**
to load backend changes. Finish active jobs before restarting. After
reconnecting, save any unfinished browser edits, then use **Reload page** to load
the updated interface. If you started Uvicorn directly, restart its process from
the terminal.

## Historical material

- [Initial implementation plan](IMPLEMENTATION_PLAN.md)
- [Implementation handoff](IMPLEMENTATION_REPORT.md)
