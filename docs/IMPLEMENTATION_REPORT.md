# ProjectHub implementation report

Status: substantial MVP implementation is present, but the requested running, fully tested MVP is **not yet verified**. Installation, browser validation and real Codex integration are blocked by this execution environment. No existing repository, Codex session, authentication file or global configuration was modified.

## Implemented in source

- Independent projects with editable metadata and read-only existing Git repository registration.
- Questions and multiple answers with evidence, advantages, limitations and sources.
- Transactional answer acceptance, linked decisions, optional tasks, idempotence and append-only audit snapshots. Reopening supersedes prior decisions while retaining alternatives and provenance.
- Standalone proposed/accepted decisions, audited revisions, linked task creation and task statuses.
- Markdown notes, structured assumptions/references, bounded UTF-8 text imports, FTS5 search, deterministic summaries and rule-based reviews.
- Manual Mermaid architecture, project JSON exports and SQLite backups.
- Codex capability detection, official App Server JSONL client, session association/start/resume, read-only tasks, versioned context, explicit decision preview/delivery/retry, event persistence, interruption and restart recovery. These operations have mocked transport/database tests; real generation has not succeeded here.
- Responsive React/TypeScript UI with project switching, status indicators, safe Markdown, proposal saving and sandboxed diagram rendering.

## Architecture and contributions

One FastAPI process serves the React build and project-scoped API. SQLAlchemy persists records in SQLite; a frozen Alembic revision establishes tables, foreign keys, indexes and FTS5 triggers. Mutable data defaults to the Linux XDG data directory. A dedicated Codex integration module owns subprocesses and delivery state.

Actual specialized subagents contributed sequentially and in parallel within available concurrency:

| Role | Contribution |
|---|---|
| Architect | Architecture, data model, API contract, phases and decisions before dependent implementation |
| Backend | API, models, transactions, migrations, storage protections and core API tests |
| Frontend | React UI, safe rendering, API client/proposal tests and source syntax checks |
| Codex integration | Installed protocol inspection, App Server lifecycle, authentication guards, delivery recovery and protocol/SQLite tests |
| Independent QA/security | Integrated code review, schema/migration/context tests, test report and security findings |
| Coordinator | Environment inspection, interface coordination, integration review, dependency investigation, startup/check/smoke scripts and documentation |

## Validation and blockers

See [TEST_REPORT.md](TEST_REPORT.md) for exact commands and distinctions between real SQLite tests, fake transport tests and blocked checks. Executable checks passed: 30 Python tests and 9 frontend API/proposal tests. Python compilation, shell syntax and TypeScript source syntax checks also passed. These checks do not establish successful HTTP workflows or a production frontend build.

The full core API suite and mocked Codex HTTP suite require unavailable FastAPI/Alembic dependencies. Package installation fails because shell networking is denied; caches also lack React, Mermaid and other necessary frontend packages. No Python resolved lockfile or npm lockfile could be produced. The real Codex handshake exits before initialization with sandbox user-namespace/read-only errors; no model turn was submitted. Git initialization and commits are blocked by the read-only `.git` placeholder.

Security review fixed missing PATCH constraints, concurrent acceptance risks, unsafe import-time data creation, upload directory handling, and the gap between CLI login status and actual App Server authentication. The Codex process forces the OpenAI provider, scrubs API-key environment overrides, verifies ChatGPT authentication, disables execution/connectors where supported and refuses generation if inherited MCP connectors remain. Approval requests fail closed. Security review remains limited by unavailable full runtime tests. See [SECURITY_REVIEW.md](SECURITY_REVIEW.md) for the local backup race hardening suggestion and trust boundaries.

## Run and validate outside the restricted execution sandbox

From the repository root in an ordinary Ubuntu terminal with registry access:

```bash
bash scripts/setup.sh
bash scripts/check.sh
.venv/bin/python scripts/smoke.py
bash scripts/start.sh
```

Open http://127.0.0.1:8766 and stop with Ctrl+C. Setup builds the frontend; start runs one loopback service. Smoke uses disposable data and verifies real HTTP, restart persistence and frontend serving. It has been written and syntax checked, but cannot run in the current environment.

Manually validate project creation/switching, question alternatives/acceptance/reopening, task changes, diagram rendering, proposal saving, and Codex read-only session/decision delivery. Do not use scientific production records until the full checks and browser flows pass.

## Assumptions and deferred work

The requested stack authorizes its necessary dependencies; Codex remains read-only throughout the MVP. Linked decision state changes go through question acceptance/reopening. Deletion is omitted to preserve knowledge. Imports support only `.md` and `.txt`, up to 2 MiB. Context uses the first 30 accepted decisions, unresolved questions and incomplete tasks in stable order, excludes imported documents, and refuses oversized packages.

Deferred/incomplete beyond the environment blockers: diagrams generated from structured component/dependency records, editable component graphs, rich document import, semantic contradiction detection, AI-specific review shortcuts (general Codex review requests are supported in source), code-editing approval workflows, automatic synchronization, JSON restore/import and multi-user/network hosting. Full UI component/browser tests and dependency locking remain required release work.
