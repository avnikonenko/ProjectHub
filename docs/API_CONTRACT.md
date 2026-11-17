# HTTP API contract v1

## General rules

All endpoints use JSON except document import and binary backup. Prefix: `/api`. IDs are integers; properties are snake_case; timestamps are UTC ISO-8601. Collection responses are arrays. Errors use FastAPI's `detail` field: 404 for absent or wrong-project objects, 422 for malformed inputs, 409 for incompatible state, 503 for unavailable integration. PATCH accepts only explicitly supported fields and validates related ownership. Required title/name/solution values must be nonblank. String lengths and request sizes are bounded.

Every abbreviated path below beginning `/{pid}` means `/api/projects/{pid}`. Frontend and backend should use precisely these properties and routes; integration-specific schema details are finalized with the Codex engineer.

## Projects and overview

- `GET /api/settings/restart`: `{available,instance_id,restarting,reason}` for this app instance. Restart is available through the managed `projecthub` launcher.
- `POST /api/settings/restart`: accepts `{}` and returns 202 `{status:"restarting",instance_id}` after reserving a graceful restart. Rejects active writes, AI generation, running Codex turns/deliveries and duplicate requests with 409; invalid configuration with 422; unmanaged launches with 503. New writes are blocked once reserved. Reconnect is confirmed only after a different, ready instance ID appears; health response fields remain unchanged.

- `GET /api/health`: availability, without secrets.
- `GET /api/projects`: project array.
- `POST /api/projects`: name required; description, goals, status, repository_path, git_remote, tags and architecture_mermaid optional.
- `GET /{pid}` and `PATCH /{pid}`: project detail and supported edits.
- `GET /{pid}/overview`: `{project, questions, decisions, tasks, notes, sessions, activity}`. Questions include nested `answers`; activity is newest first; other collections are stable ID order.
- `GET /{pid}/overview?compact=true&note_content=false`: lightweight browser loading; omits retired workflow collections, note content and activity before/after bodies. Note metadata includes `content_loaded:false`. `GET /{pid}/notes/{id}` retrieves full project-scoped content on demand. Existing overview defaults retain full data.

## Resource bounds and exports

- `GET /{pid}/audit` retains the complete array response by default; optional `offset` and `limit` (1–1000) page through it. Audit and project export responses are assembled into private temporary files and sent in 64 KiB chunks, retaining neither all historical entities nor the entire encoded response in memory.
- `GET /{pid}/export` preserves its existing fields and `schema_version:1`, and adds `brief`, `qa`, `scheme`, `knowledge`, `ai_instructions`, `generation_revisions`, `history_batches`, `history_turns` and `history_index`. Singleton values are serialized saved rows or null. All rows are scoped to the project; brief and Knowledge are read in the same SQLite transaction. Managed upload files and global Codex authentication are not included; the application-data backup remains necessary for file-level restoration.
- Note `content` supports up to 16 MiB of UTF-8 text and preserves original whitespace, matching imported transcript limits. Other string constraints remain unchanged. Only note POST/PATCH routes permit the larger JSON envelope needed for escaped text; other mutation requests retain the 3 MiB body limit. Partial PATCH remains supported.
- Shared-chat extraction enforces that byte limit after expanding indexed references and includes role labels and separators; the import endpoint validates the final result again before storing it. The note editor sends only changed fields, so existing oversized originals remain readable and can receive metadata edits without truncation. Changed content must fit the current limit.
- Knowledge source-label search uses a materialized SQLite CTE (SQLite 3.35+). Revision summaries select generation metadata without loading all historical snapshots. Repository inspection enforces both output-byte and time limits and reports failures rather than returning incomplete counts.

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
  Status validation and task creation share a serialized write transaction with acceptance/reopening.
- `POST /{pid}/tasks`: `{title,description?,priority?,status?,question_id?,decision_id?,session_id?}`.
- `PATCH /{pid}/tasks/{tid}`: same editable fields.

Decision: `{id,project_id,title,description,next_step,rationale,alternatives,evidence,limitations,status,question_id,answer_id,created_at,updated_at}`.

Task: `{id,project_id,title,description,priority,status,question_id,decision_id,session_id,created_at,updated_at}`.

## Knowledge and derived views

- `GET /{pid}/codex/scheme` keeps its saved snapshot and freshness fields; current `inspection` also returns optional `diagnostics`. New `coverage.file_counts` records Git-listed paths, explicit exclusions, safe paths, identified candidates, attempted and decoded reads, prefix-only reads, failed/unattempted reads, and distinct repository files retained in the final evidence package. Unknown listing totals are null; ignored untracked files are not enumerated. `coverage.inspection_policy` saves the numeric limits used. Saved snapshots/revisions retain their own counts; old snapshots are not backfilled from current inspection. Counts do not measure semantic completeness.
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

- `GET /{pid}/codex/brief`: latest saved brief, or `null`. This never requests generation.
- `GET /{pid}/codex/revisions?kind=brief|qa&offset=0`: ten newest revision summaries and `next_offset`. `GET /{pid}/codex/revisions/{id}` returns the project-scoped `{kind,snapshot}`; wrong-project IDs return 404. Successful generations append snapshots atomically, while failed generations append nothing. Existing latest snapshots are seeded during migration.
- `GET /{pid}/codex/sessions/{sid}/responses?offset=0&limit=3`: completed assistant responses, separately paginated from the raw event log. Returns `{events,total,latest_id}`; each page is oldest-to-newest within a newest-first page window. Limit is 1–50; cross-project sessions return 404. The raw `/events` endpoint retains its 500-event bound.
- Saved brief/Q&A responses include nullable `generation:{account,model,effort}` metadata for the snapshot. Both generation paths include project-scoped notes and AI notes as untrusted reference sources.
- `GET /{pid}/codex/generation/status?kind=brief|qa`: process-local progress for that project and kind, with `state`, `stage`, `sessions`, `turns`, `batches`, and nullable `error`. Status resets on restart; successful snapshot metadata persists.
- `GET /{pid}/codex/account`: returns the project's selected account key and available local profile labels. `PATCH` with `{account:string|null}` saves the selection; null uses the launch account. Unknown profiles are rejected. Switching while generation or a managed turn is running is rejected.
- `GET /{pid}/codex/models`: lists visible models and their supported `efforts` from the signed-in Codex account. Brief and Q&A generation, Codex turns, and explicit delivery attempts accept optional `model` and `effort` overrides. Effort requires a selected model. Omitted overrides use Codex defaults for generation or the current session defaults for a discussion.
- `GET /{pid}/codex/qa`: latest saved Q&A, or `null`. `POST` with `{confirmed:true}` generates a separate snapshot from linked conversation evidence in batches. Returns `{generated_at,items:[{question,answer,source_ids}],sources}`. A failed refresh keeps the prior Q&A.
- `POST /{pid}/codex/brief`: `{confirmed:true}`; pages through saved text from all linked sessions and summarizes it in bounded, cached batches. The final generation also uses accepted decisions and the saved architecture diagram. Successful generation replaces the saved brief and map; errors keep the previous snapshot.
- A brief contains `generated_at`, `sections` (overview, architecture, changes, open questions, and unconfirmed decision candidates), `sources`, `coverage`, and `project_map`. Older briefs may lack an architecture section or have a null map. Section entries and map nodes and edges cite original turn, accepted decision, or diagram fragments via `source_ids`. Map edges mark inferred connections. Generating a brief does not create decisions, resume source sessions, or scan the repository.

- `POST /{pid}/codex/sessions` without `thread_id`: starts an app-owned read-only discussion session.
- `POST /{pid}/codex/sessions` with `thread_id`: validates the thread with `thread/read`, stores status `linked`, closes the temporary connection and does not resume or submit anything.
- `GET /{pid}/codex/sessions/{sid}/history?cursor=...&limit=3`: returns `{turns:[{id,status,messages:[{role,text}]}],next_cursor}` from bounded App Server history pages. The limit is 1–5 (API default 5; the UI uses 3). Only user and assistant display text is returned; tool output is excluded.
- `POST /{pid}/codex/history/index`: refreshes the local text index of finished turns across linked sessions and returns `{indexed_at,indexed_turns}`. This does not request AI generation. A failed refresh preserves the previous index.
- `GET /{pid}/codex/history/search?q=...&offset=0`: searches the index and returns `{results,next_offset,indexed_at,indexed_turns}`. Results have `session_id`, `session_title`, `turn_id`, and a plain-text `snippet`.
- `GET /{pid}/codex/sessions/{sid}/history/turn?turn_id=...`: opens an exact indexed turn, returning `{id,status,messages:[{role,text}]}`. Returns 404 for turns absent from the project's index.
- `POST /{pid}/codex/sessions/{sid}/resume`: explicitly attempts to take ownership for later read-only discussion. Linked sessions reject tasks and decision delivery until this succeeds.

Protocol/API absence is reported as a limitation rather than emulated success. API responses must not include tokens, environment dumps, or raw global config.

## Frozen derived response shapes

- Summary: `{project_id,generated_at,markdown}`.
- Review: `{project_id,generated_at,findings:[{severity,title,detail}],markdown}`; severity is `info` or `warning`.
- Search: array of `{entity_type,entity_id,title,snippet}` ordered by rank then stable IDs.
- Export: `{schema_version:1,project,questions,answers,decisions,tasks,notes,audit,sessions,contexts,deliveries,events}`; only selected-project records.
- Documents: multipart `file`, maximum 2 MiB, `.txt`/`.md` UTF-8 text only in MVP. Reject binary/invalid UTF-8. Response is document-kind Note; `reference` is a managed storage filename, never arbitrary local path. This limited text import avoids parsing dependencies; richer formats are deferred.

Integration persistence names agreed with backend: `CodexSession` (thread_id, title, status, last_error), `ContextPackage` (content, version as SHA-256 hash), `Delivery` (decision_id, context_id, content, status, error, turn_id), `CodexEvent` (method, payload), each with project/session foreign keys and timestamps as appropriate.
