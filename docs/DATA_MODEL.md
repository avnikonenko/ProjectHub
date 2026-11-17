# Data model

## Conventions

Identifiers are integer primary keys. Timestamps are UTC ISO-8601 strings. Each mutable knowledge record has creation/update timestamps. Every child belongs to a project. Relations are validated against that project before read or mutation; foreign keys and transactions provide a second consistency boundary. Index project IDs and foreign keys. Stable ID ordering makes summaries and exports reproducible.

## Records

| Record | Principal fields and relations |
| --- | --- |
| Project | name, description, goals, status, repository_path, git_remote, tags (JSON list), architecture_mermaid, timestamps |
| Question | project_id, title, description, category, priority, status, accepted_answer_id (nullable), timestamps |
| Answer | project_id, question_id, solution, explanation, advantages, limitations, evidence, source, status, created_at |
| Decision | project_id, question_id/answer_id (nullable), title, description, rationale, alternatives, evidence, limitations, status, timestamps |
| Task | project_id, question_id/decision_id/session_id (nullable), title, description, priority, status, timestamps |
| Note | project_id, title, content, kind, reference, optional managed document metadata, timestamps |
| Audit event | project_id, action, entity_type, entity_id, immutable before/after snapshot, created_at |
| Codex session | project_id, external thread ID, label, status, last error, timestamps |
| Context snapshot | project_id, session_id, exact payload, content hash/version, created_at |
| Codex delivery/run | project_id, session_id, context snapshot reference, preview/prompt, external turn ID, status, error, timestamps |
| Codex event | project_id, session_id, run reference, method, bounded event payload, created_at |

FTS5 indexes project-scoped searchable knowledge (questions, answers, decisions, tasks, notes). Search results include entity type, ID, title, snippet and project ID; queries always filter project ownership. Rebuilding the index is possible from primary records.

## Enumerations

- Question: `open`, `under_discussion`, `awaiting_validation`, `resolved`, `reopened`.
- Answer: `proposed`, `accepted`; historical acceptance lives in audit events and decisions.
- Decision: `proposed`, `accepted`, `superseded`, `rejected`.
- Task: `backlog`, `ready`, `in_progress`, `blocked`, `completed`.
- Priority: `low`, `medium`, `high`, `critical`.
- Note kind: `note`, `technical_assumption`, `scientific_assumption`, `evidence`, `limitation`, `document`.
- Delivery: `queued`, `submitted`, `completed`, `failed` (and `interrupted` for runs when supported).

## Consistency rules

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

The MVP retains audit and delivery history. Exports include project knowledge and provenance; managed upload contents require the application-data backup. No automatic retention or destructive cleanup runs in the background.
