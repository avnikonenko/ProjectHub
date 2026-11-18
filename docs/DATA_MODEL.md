# Data model

## Conventions

User-managed records have integer IDs and UTC ISO-8601 timestamps. Saved views
are keyed by project, and generation attempts by project and view kind. Reads
and writes validate related records against the project. Foreign keys and
transactions provide a second consistency boundary.

## Records

| Record | Principal fields and relations |
| --- | --- |
| Project | name, description, goals, status, pinned, repository_path, git_remote, tags (JSON list), architecture_mermaid, codex_account, timestamps |
| Question | project_id, title, description, category, priority, status, accepted_answer_id (nullable), timestamps |
| Answer | project_id, question_id, solution, explanation, advantages, limitations, evidence, source, status, created_at |
| Decision | project_id, question_id/answer_id (nullable), title, description, next_step, rationale, alternatives, evidence, limitations, status, timestamps |
| Task | project_id, question_id/decision_id/session_id (nullable), title, description, priority, status, pinned, timestamps |
| Note | project_id, title, content, kind, reference (which can identify a managed document), timestamps |
| Audit event | project_id, action, entity_type, entity_id, immutable before/after snapshot, created_at |
| Codex session | project_id, external thread ID, title, topic, Codex account, link and sync flags, status, last error, timestamps |
| Context snapshot | project_id, session_id, exact payload, content hash/version, created_at |
| Codex delivery | project_id, session_id, optional context or decision reference, content, external turn ID, status, error, timestamps |
| Codex event | project_id, session_id, method, event payload, created_at |
| Saved views | Current Overview, Q&A, Scheme, and Knowledge stored by project. Knowledge includes its detailed summary. |
| Generation revision | project_id, kind, generated_at, snapshot. The latest `parameters` revision is the saved Implementation Choices & Limits view. |
| Generation attempt | project_id, kind, started_at, finished_at, state, error, saves_result |

FTS5 indexes project records and saved conversation turns. Project search
groups results by kind, returning IDs, titles and snippets with navigation
links where available. Queries filter by project ownership.

## Enumerations

- Question: `open`, `under_discussion`, `awaiting_validation`, `resolved`, `reopened`.
- Answer: `proposed`, `accepted`; historical acceptance lives in audit events and decisions.
- Decision: `proposed`, `accepted`, `superseded`, `rejected`.
- Task: `backlog`, `ready`, `in_progress`, `blocked`, `completed`, `not_planned`.
- Priority: `low`, `medium`, `high`, `critical`.
- Note kind: `note`, `ai_note`, `assumption`, `technical_assumption`, `scientific_assumption`, `evidence`, `limitation`, `document`, `proposal`.
- Delivery: `draft`, `queued`, `sending`, `sent`, `completed`, `failed`, `unverified`, `interrupted`, `cancelled`.

## Consistency rules

The question and answer rules below apply to the legacy acceptance API.

1. An accepted answer belongs to the question and project, and at most one answer is currently accepted per question.
2. Acceptance selects a current answer and creates an accepted linked decision atomically. Repeating the current acceptance is idempotent and cannot duplicate its task.
3. Choosing a different answer supersedes the old accepted decision and preserves its complete rationale and evidence. Alternative answers are retained.
4. Reopening explicitly supersedes the prior linked accepted decision, clears current acceptance and returns the selected answer to proposed status; history remains visible.
5. Direct decision revisions append audit snapshots. A standalone proposed decision can become accepted only through an explicit status change.
6. Related task/question/decision/session references must all belong to the same project, including PATCH operations.
7. Sessions carry project association independently of external identifiers. The same external thread must not silently mix context from different projects.
8. A delivery records the exact reviewed update and context snapshot. Retrying preserves provenance and never silently replaces the reviewed content.
9. Deleting a ProjectHub record cannot trigger filesystem deletion of a registered repository.

## Transactions and migrations

Alembic tracks schema revisions. Startup initializes a new database through migrations; deployment instructions describe upgrades. All multi-record state transitions occur in one transaction and roll back on validation or persistence failure. SQLite foreign keys are enabled per connection and busy timeout is bounded. Backup uses the SQLite backup API rather than copying an active WAL database file.

## Retention

Audit and delivery history are retained. Project exports include Knowledge and
provenance. Back up the application data directory to preserve managed upload
files alongside the database.
