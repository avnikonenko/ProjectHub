# Validation guide

Run from the source checkout:

```bash
bash scripts/setup.sh
bash scripts/check.sh
.venv/bin/python scripts/smoke.py
```

The check script runs Python tests, frontend API/helper tests, TypeScript checking,
and the production frontend build. Python tests exercise real SQLite, migrations,
FTS5, HTTP project isolation, task maintenance, and persistence. Codex transport and
manager tests generally use fake subprocesses or App Server clients; they do not
establish real model behavior. Frontend helper tests do not render React in a browser.

Before a release, install from a clean checkout with temporary XDG data/config
directories, exercise import/search/TODO/review flows, and restart to check
persistence. Separately verify Codex sign-in and read-only generation. Do not
mistake a queued delivery for a completed response.

Record the commit, environment, commands, results, and whether AI was mocked or
live. A passing automated suite does not establish extraction accuracy or
scientific validity. Run checks in the intended release environment rather than
treating an old report as evidence for the current version.
