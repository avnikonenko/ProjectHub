# Developer reference

- [Architecture](ARCHITECTURE.md), [HTTP API](API_CONTRACT.md), and [data model](DATA_MODEL.md)
- [Knowledge pipeline](KNOWLEDGE_PIPELINE.md), [Knowledge interface](KNOWLEDGE_UI_INTERNALS.md), and [Scheme implementation](scheme-implementation.md)
- [Security boundaries](SECURITY_REVIEW.md) and [engineering decisions](DECISIONS.md)

## Validate a release

Run these from a clean checkout.

```bash
bash scripts/setup.sh
bash scripts/check.sh
.venv/bin/python scripts/smoke.py
```

- **Automated checks** cover backend and frontend tests, TypeScript, and the production build.
- **Browser checks** use temporary XDG directories to verify import, search, TODOs, reviews, and persistence after restart.
- **Live Codex checks** verify sign-in and a completed read-only generation with the installed Codex version.

Record the commit, environment, results, and which checks used simulated services.

## Preview the documentation

Use a separate Python environment from the repository root.

```bash
python -m pip install -r docs/requirements.txt
python -m mkdocs serve
```

Check for warnings with `python -m mkdocs build --strict`.

## Build a wheel

Use Node.js 22.18+ and npm from a source checkout.

```bash
npm --prefix frontend ci
npm --prefix frontend run build
python3 -m pip wheel --no-deps --wheel-dir dist .
```

[Install the wheel](GETTING_STARTED.md#install-a-wheel) and test it with an isolated data directory.

## Reload local development changes

Finish active jobs, then use **Settings → Restart ProjectHub** to load backend
changes. If you started Uvicorn directly, restart it from the terminal. After
reconnecting, save unfinished edits before reloading the page.
