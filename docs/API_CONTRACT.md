# HTTP API contract v1

## General rules

All endpoints use JSON except document import and binary backup. Prefix: `/api`. IDs are integers; properties are snake_case; timestamps are UTC ISO-8601. Collection responses are arrays. Errors use FastAPI's `detail` field: 404 for absent or wrong-project objects, 422 for malformed inputs, 409 for incompatible state, 503 for unavailable integration. PATCH accepts only explicitly supported fields and validates related ownership. Required title/name/solution values must be nonblank. String lengths and request sizes are bounded.

Every abbreviated path below beginning `/{pid}` means `/api/projects/{pid}`. Frontend and backend should use precisely these properties and routes; integration-specific schema details are finalized with the Codex engineer.

## Projects and overview

- `GET /api/health`: availability, without secrets.
- `GET /api/projects`: project array.
- `POST /api/projects`: name required; description, goals, status, repository_path, git_remote, tags and architecture_mermaid optional.
- `GET /{pid}` and `PATCH /{pid}`: project detail and supported edits.
- `GET /{pid}/overview`: `{project, questions, decisions, tasks, notes, sessions, activity}`. Questions include nested `answers`; activity is newest first; other collections are stable ID order.

Project: `{id,name,description,goals,status,repository_path,git_remote,tags,architecture_mermaid,created_at,updated_at}`.

## Questions and answers

- `POST /{pid}/questions`: `{title,description?,category?,priority?,status?}`; returns question with answers.
- `PATCH /{pid}/questions/{qid}`: title, description, category, priority and compatible nonterminal statuses. Resolving/reopening uses explicit lifecycle endpoints.
- `POST /{pid}/questions/{qid}/answers`: `{solution,explanation?,advantages?,limitations?,evidence?,source?}`; returns answer.
- `POST /{pid}/questions/{qid}/accept`: `{answer_id,next_step?,create_task:false}`; returns `{question,decision,task}` (`task` nullable). Transactional and idempotent for current answer; use the decision PATCH to edit its next step.
- `POST /{pid}/questions/{qid}/reopen`: `{}`; returns question. Preserves and supersedes previous decision.

Question: `{id,project_id,title,description,category,priority,status,accepted_answer_id,created_at,updated_at,answers}`.

Answer: `{id,project_id,question_id,solution,explanation,advantages,limitations,evidence,source,status,created_at}`.

## Decisions and tasks

- `POST /{pid}/decisions`: `{title,description?,next_step?,rationale?,alternatives?,evidence?,limitations?,status?}`; status defaults to proposed and the optional next step is at most 500 characters.
- `PATCH /{pid}/decisions/{did}`: editable decision fields/status; always preserves before/after audit history.
- `POST /{pid}/decisions/{did}/tasks`: `{title?,description?,priority?}`; requires accepted decision, returns linked task.
- `POST /{pid}/tasks`: `{title,description?,priority?,status?,question_id?,decision_id?,session_id?}`.
- `PATCH /{pid}/tasks/{tid}`: same editable fields.

Decision: `{id,project_id,title,description,next_step,rationale,alternatives,evidence,limitations,status,question_id,answer_id,created_at,updated_at}`.

Task: `{id,project_id,title,description,priority,status,question_id,decision_id,session_id,created_at,updated_at}`.

## Knowledge and derived views

- `POST /{pid}/notes`: `{title,content?,kind?,reference?}`.
- `PATCH /{pid}/notes/{nid}`: note edits.
- `POST /{pid}/documents`: bounded document import; exact encoding selected by backend/frontend together. Response is a document-kind note, never executed content.
- `GET /{pid}/summary`: deterministic structured project summary, with Markdown presentation.
- `GET /{pid}/review`: deterministic review of incomplete tasks, open questions, missing evidence/validation and recorded limitations. Findings distinguish records from suggestions.
- `GET /{pid}/search?q=...`: project-scoped FTS5 search results.
- `GET /{pid}/audit`: append-only activity/revision events.
- `GET /{pid}/export`: JSON containing only selected-project records and provenance.
- `POST /api/backup`: consistent SQLite backup in managed data directory; returns backup metadata or download identifier. Exact operational route is coordinator-owned.

Summary/review rendering and upload encoding are integration details to freeze before frontend uses them; they must not require AI access.

## Codex interface

Integration router prefix: `/api/projects/{project_id}/codex`; global capability probe may use `/api/codex/status`. Codex engineer coordinates exact payloads and polling/event behavior before frontend wiring.

Required operations: capability/auth availability, list project associations, create a read-only discussion thread, link an existing thread without resuming it, read its saved history, explicitly resume it when sending is required, preview context/task, submit reviewed task, poll status/events, preview decision update, enqueue explicit delivery/retry, and save output as proposed knowledge. Never submit on answer acceptance. Busy/unavailable sessions preserve a local queue and show unverified delivery state. Session and delivery lookups are project scoped.

- `POST /{pid}/codex/sessions` without `thread_id`: starts an app-owned read-only discussion session.
- `POST /{pid}/codex/sessions` with `thread_id`: validates the thread with `thread/read`, stores status `linked`, closes the temporary connection and does not resume or submit anything.
- `GET /{pid}/codex/sessions/{sid}/history?cursor=...`: returns `{turns:[{id,status,messages:[{role,text}]}],next_cursor}` from bounded App Server history pages. Only user and assistant display text is returned; tool output is excluded.
- `POST /{pid}/codex/sessions/{sid}/resume`: explicitly attempts to take ownership for later read-only discussion. Linked sessions reject tasks and decision delivery until this succeeds.

Protocol/API absence is reported as a limitation rather than emulated success. API responses must not include tokens, environment dumps, or raw global config.

## Frozen derived response shapes

- Summary: `{project_id,generated_at,markdown}`.
- Review: `{project_id,generated_at,findings:[{severity,title,detail}],markdown}`; severity is `info` or `warning`.
- Search: array of `{entity_type,entity_id,title,snippet}` ordered by rank then stable IDs.
- Export: `{schema_version:1,project,questions,answers,decisions,tasks,notes,audit,sessions,contexts,deliveries,events}`; only selected-project records.
- Documents: multipart `file`, maximum 2 MiB, `.txt`/`.md` UTF-8 text only in MVP. Reject binary/invalid UTF-8. Response is document-kind Note; `reference` is a managed storage filename, never arbitrary local path. This limited text import avoids parsing dependencies; richer formats are deferred.

Integration persistence names agreed with backend: `CodexSession` (thread_id, title, status, last_error), `ContextPackage` (content, version as SHA-256 hash), `Delivery` (decision_id, context_id, content, status, error, turn_id), `CodexEvent` (method, payload), each with project/session foreign keys and timestamps as appropriate.
