# Implementation plan

## Assumptions and constraints

Use this directory only. Required dependencies are authorized by the requested stack. The initial working directory is empty apart from sandbox-provided read-only metadata directories and the session resume note. A read-only `.git` placeholder may prevent repository initialization/commits; report that constraint without removing it. Node/npm may need explicit discovery outside PATH. Python is 3.14, so dependency versions must be validated rather than assuming 3.11 wheel availability.

Codex is installed (CLI 0.166.0) and reports ChatGPT authentication. This is capability discovery, not proof that turns can run in this sandbox. Never inspect credential contents. Actual protocol availability must be demonstrated.

## Milestone 1: interfaces

Architect writes architecture, model, API contract and lifecycle invariants. Coordinator freezes response properties and delegates clear ownership. Acceptance: all implementers have the same routes, enums, entities and transaction rules.

## Milestone 2: independent implementations

After the contract is frozen, run at most three implementation agents:

1. Backend: migrations, relational persistence, project isolation, lifecycle, notes/documents, FTS5, deterministic summaries/reviews and backend tests.
2. Frontend: React project selector, dashboard, questions/answers/acceptance, decision register, task table, knowledge/search, Mermaid, session screens and UI tests.
3. Codex: installed capability verification, official protocol adapter, read-only process lifecycle, context snapshots, preview/delivery queue and isolated integration tests.

Coordinator owns packaging, shared configuration, startup scripts and README. Implementers report completed work, tests, blockers and outstanding issues.

Acceptance: core workflow runs without Codex and each component passes its focused tests.

## Milestone 3: integration

Wire one-origin serving, bundle responses, app lifespan, migrations, imports/exports/backups, safe browser requests and Codex adapter routes. Verify persistence on restart and independent project state. Resolve shared changes centrally.

Acceptance: a user can create two projects, register an existing repository unchanged, accept/reopen answers, create tasks, render architecture and retrieve deterministic summary/review.

## Milestone 4: independent QA and security

After integration, assign a QA/security agent independent tests and review. Required cases: project CRUD/isolation, multi-answer acceptance, audit snapshots, reopening/superseding, task links, restart persistence, summary, traversal/symlink protection, unavailable Codex, protocol error handling, no API-key requirement and browser build/tests.

A real Codex smoke test verifies handshake/session creation and, when permitted, a bounded read-only turn. Record separately from mocked tests. Failures must remain visible and may not be reported as working integration.

Acceptance: all tests pass, critical defects fixed and reviewed, exact startup verified, remaining limitations documented.

## Final handoff

Report functional scope, agent contributions, architecture, test results, security findings, startup command, environment blockers and deferred behavior. Do not label an untested operation verified. Source and artifacts remain available even if sandbox restrictions prevent Git commits or default XDG writes.
