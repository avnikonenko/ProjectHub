# HTTP API contract v1

## General rules

Responses use JSON unless the route returns 204. Document upload uses multipart form data. IDs are integers, properties use snake_case, and timestamps use UTC ISO-8601. Error responses use FastAPI's `detail` field. Common codes are 404 for absent or wrong-project objects, 422 for invalid input, 409 for incompatible state, and 503 for an unavailable integration. PATCH accepts explicitly supported fields and validates related ownership. Required names, titles, and solutions must be nonblank.

Every path below beginning `/{pid}` means `/api/projects/{pid}`.

## Projects and overview

- `GET /api/settings/restart`: `{available,instance_id,restarting,reason}` for this app instance. Restart is available through the managed `projecthub` launcher.
- `POST /api/settings/restart`: accepts `{}` and returns 202 `{status:"restarting",instance_id}` after reserving a graceful restart. Rejects active writes, AI generation, running Codex turns/deliveries and duplicate requests with 409; invalid configuration with 422; unmanaged launches with 503. New writes are blocked once reserved. Reconnect is confirmed only after a different, ready instance ID appears; health response fields remain unchanged.

- `GET /api/health`: availability, without secrets.
- `GET /api/projects`: project array ordered by pin first, then ID. With `compact=true`, returns only `{id,name,pinned}` for each project.
- `POST /api/projects`: requires `name`. Accepts optional `pinned`, `description`, `goals`, `status`, `repository_path`, `git_remote`, `tags`, and `architecture_mermaid`.
- `GET /{pid}` and `PATCH /{pid}`: project detail and supported edits.
- `DELETE /{pid}`: permanently removes the project and its saved ProjectHub records. The app asks for confirmation. Registered repository files and original Codex history stay in place. Returns 204 on success, 404 for a missing project, and 409 while conflicting work is active.
- `GET /{pid}/overview`: `{project,questions,decisions,tasks,notes,sessions,activity}`. Questions include nested `answers`. Tasks are ordered by pin first, then ID. Activity contains up to 50 newest entries.
- `GET /{pid}/overview?compact=true&note_content=false`: lightweight browser loading; omits retired workflow collections, note content and activity before/after bodies. Note metadata includes `content_loaded:false`. `GET /{pid}/notes/{id}` retrieves full project-scoped content on demand. Existing overview defaults retain full data.

## Resource bounds and exports

- `GET /{pid}/audit` retains the complete array response by default; optional `offset` and `limit` (1–1000) page through it. Audit and project export responses are assembled into private temporary files and sent in 64 KiB chunks, retaining neither all historical entities nor the entire encoded response in memory.
- `GET /{pid}/export` preserves its existing fields and `schema_version:1`, and adds `brief`, `qa`, `scheme`, `knowledge`, `ai_instructions`, `generation_revisions`, `history_batches`, `history_turns` and `history_index`. Singleton values are serialized saved rows or null. All rows are scoped to the project; brief and Knowledge are read in the same SQLite transaction. Managed upload files and global Codex authentication are not included; the application-data backup remains necessary for file-level restoration.
- Note `content` supports up to 16 MiB of UTF-8 text and preserves original whitespace, matching imported transcript limits. Other string constraints remain unchanged. Only note POST/PATCH routes permit the larger JSON envelope needed for escaped text; other mutation requests retain the 3 MiB body limit. Partial PATCH remains supported.
- Shared-chat extraction enforces that byte limit after expanding indexed references and includes role labels and separators; the import endpoint validates the final result again before storing it. The note editor sends only changed fields, so existing oversized originals remain readable and can receive metadata edits without truncation. Changed content must fit the current limit.
- Knowledge source-label search uses a materialized SQLite CTE (SQLite 3.35+). Revision summaries select generation metadata without loading all historical snapshots. Repository inspection enforces both output-byte and time limits and reports failures rather than returning incomplete counts.

Project fields are `{id,name,pinned,codex_account,description,goals,status,repository_path,git_remote,tags,architecture_mermaid,created_at,updated_at}`. Set the account through the Codex account route.

## Legacy questions and answers

- `POST /{pid}/questions`: `{title,description?,category?,priority?,status?}`; returns question with answers.
- `PATCH /{pid}/questions/{qid}`: title, description, category, priority and compatible nonterminal statuses. Resolving/reopening uses explicit lifecycle endpoints.
- `POST /{pid}/questions/{qid}/answers`: `{solution,explanation?,advantages?,limitations?,evidence?,source?}`; returns answer.
- `POST /{pid}/questions/{qid}/accept`: `{answer_id,next_step?,create_task:false}`; returns `{question,decision,task}` (`task` nullable). Transactional and idempotent for current answer; use the decision PATCH to edit its next step.
- `POST /{pid}/questions/{qid}/reopen`: `{}`; returns question. Preserves and supersedes previous decision.

Question: `{id,project_id,title,description,category,priority,status,accepted_answer_id,created_at,updated_at,answers}`.

Answer: `{id,project_id,question_id,solution,explanation,advantages,limitations,evidence,source,status,created_at}`.

## Tasks and legacy decisions

- `POST /{pid}/decisions`: `{title,description?,next_step?,rationale?,alternatives?,evidence?,limitations?,status?}`; status defaults to proposed and the optional next step is at most 500 characters.
- `PATCH /{pid}/decisions/{did}`: editable decision fields/status; always preserves before/after audit history.
- `POST /{pid}/decisions/{did}/tasks`: `{title?,description?,priority?}`; requires accepted decision, returns linked task.
  Status validation and task creation share a serialized write transaction with acceptance/reopening.
- `POST /{pid}/tasks`: `{title,pinned?,description?,priority?,status?,question_id?,decision_id?,session_id?}`.
- `PATCH /{pid}/tasks/{tid}`: same editable fields.

Decision: `{id,project_id,title,description,next_step,rationale,alternatives,evidence,limitations,status,question_id,answer_id,created_at,updated_at}`.

Task fields are `{id,project_id,pinned,title,description,priority,status,question_id,decision_id,session_id,created_at,updated_at}`.

## Knowledge and derived views

- `GET /{pid}/codex/scheme` keeps its saved snapshot and freshness fields; current `inspection` also returns optional `diagnostics`. New `coverage.file_counts` records Git-listed paths, explicit exclusions, safe paths, identified candidates, attempted and decoded reads, prefix-only reads, failed/unattempted reads, and distinct repository files retained in the final evidence package. Unknown listing totals are null; ignored untracked files are not enumerated. `coverage.inspection_policy` saves the numeric limits used. Saved snapshots/revisions retain their own counts; old snapshots are not backfilled from current inspection. Counts do not measure semantic completeness.
  Current inspection and new snapshots include `dependency_inventory:{dependencies,sources,coverage}`. It contains deterministic manifest declarations and otherwise undeclared Python imports with local evidence. Coverage reports `files_total` (nullable), `files_scanned`, and `limitations`. The existing `dependencies` array remains the selection supplied to the model for explanations.
- `POST /{pid}/notes`: `{title,content?,kind?,reference?}`.
- `PATCH /{pid}/notes/{nid}`: note edits.
- `POST /{pid}/documents`: multipart `file` with a safe `.txt` or `.md` filename and up to 2 MiB of UTF-8 text. Returns a document-kind note whose `reference` is the managed storage filename.
- `GET /{pid}/summary`: deterministic `{project_id,source,sections,markdown,generated_at}`.
- `GET /{pid}/review`: deterministic `{project_id,source,findings,limitations,generated_at,markdown}`. Findings cover incomplete tasks and limitation notes. Each finding includes `category`, `message`, `entity_id`, `status`, `severity`, `title`, and `detail`.
- `GET /{pid}/search?q=...`: up to 100 project-scoped FTS5 matches with `{entity_type,entity_id,title,snippet}`, ordered by rank, then type and ID.
- `GET /{pid}/search/all?q=...&kind=all&offset=0`: grouped project search with a `groups` array. Each group has `kind`, `title`, `results`, and `next_offset`.
- `POST /api/backup`: writes a consistent SQLite copy under the managed data directory and returns `{filename,path}`.

## Codex interface

- `GET /{pid}/codex/status` reports capability and authentication availability for the project's selected account. Session and delivery lookups are project scoped. Accepting an answer does not send it to Codex.

- `GET /{pid}/codex/brief`: latest saved brief, or `null`. This never requests generation.
- `GET /{pid}/codex/revisions?kind=brief&offset=0`: `{results,next_offset}` with up to ten newest revision summaries. Supported kinds are `brief`, `qa`, `scheme`, `knowledge`, and `parameters`. `GET /{pid}/codex/revisions/{id}` returns `{kind,snapshot}` for a project-owned revision. Successful generations save revisions atomically.
- `GET /{pid}/codex/sessions/{sid}/responses?offset=0&limit=3`: completed assistant responses, separately paginated from the raw event log. Returns `{events,total,latest_id}`; each page is oldest-to-newest within a newest-first page window. Limit is 1–50; cross-project sessions return 404. The raw `/events` endpoint retains its 500-event bound.
- Saved brief/Q&A responses include nullable `generation:{account,model,effort}` metadata for the snapshot. Both generation paths include project-scoped notes and AI notes as untrusted reference sources.
- `GET /{pid}/codex/generation/status?kind=brief`: progress for `brief`, `qa`, `scheme`, `knowledge`, `ask`, `tags`, or `parameters`. Includes `state`, `stage`, counters, `error`, and `latest_attempt`. Detailed progress is process-local. The latest recorded attempt persists, and running or waiting attempts are marked interrupted at startup.
- `GET /{pid}/codex/generation/summary`: `{project_id,views}` with saved-result metadata and the latest attempt for each of Overview, Knowledge, Scheme, Q&A, and Implementation Choices.
  The latest attempt includes nullable `settings` captured for that run. It contains `account`, `model` and `effort`, and may include `generation_profile`, `generation_budget` (seconds), `project_instructions` (whether custom instructions were configured), `extraction_workers`, `max_items` (Q&A), and the Scheme limits `scheme_source_chars`, `scheme_read_bytes` and `scheme_package_chars`. Additional settings are recorded when the job configures the relevant stages. Older attempts can have no recorded settings.
- `GET /{pid}/codex/account`: returns the project's selected account key and available local profile labels. `PATCH` with `{account:string|null}` saves the selection; null uses the launch account. Unknown profiles are rejected. Switching while generation or a managed turn is running is rejected.
- `GET /{pid}/codex/models`: lists models and supported `efforts`. By default it lists models from the signed-in Codex account. Pass `?provider=claude` to list the supported Claude Code choices.
- `GET /api/settings/ai-profiles?provider=claude` lists registered Claude Code profile labels and IDs plus the launch account label. `POST /api/settings/ai-profiles` accepts `provider` (`codex` or `claude`), `name`, and the absolute `directory` of an existing CLI sign-in. The existing `/api/settings/codex-profiles` registration endpoint remains supported.
- AI generation requests for Overview, Knowledge, Q&A, Scheme, Implementation Choices & Limits, tags, and Ask Project accept optional `provider` (`codex` by default, or `claude`) with `model` and `effort` overrides. Claude requests also accept `ai_profile`, a registered Claude Code profile ID. Omitting it uses the launch account. Codex turns and explicit delivery attempts keep their existing model and effort overrides and use Codex.
- `GET /{pid}/codex/qa`: latest saved Q&A or `null`, with items, sources, and saved generation and topic metadata when available. `POST` with `{confirmed:true}` generates a separate snapshot in batches. A failed refresh keeps the prior Q&A.
- `POST /{pid}/codex/brief` requires `confirmed:true`. It reads linked history in bounded, cached batches and uses project records and inspected repository evidence when available. ProjectHub inspects the linked repository locally without running its code or tests. Successful generation saves the Overview and its revision. Errors preserve the previous snapshot.
- An Overview includes `generated_at`, `sections`, `sources`, `coverage`, `project_map`, and `generation`. Current sections are `overview`, `architecture`, `changes`, `since_last_brief`, `current_status`, `implemented`, `unresolved_problems`, `validation`, and `next_steps`. The response retains `open_questions`, `decision_candidates`, and `work_in_progress` for compatibility. Section entries and map elements cite source IDs.
- For Knowledge routes, see the [Knowledge pipeline reference](KNOWLEDGE_PIPELINE.md#generation-and-publication).

- `POST /{pid}/codex/sessions` without `thread_id`: starts an app-owned read-only discussion session.
- `POST /{pid}/codex/sessions` with a new `thread_id`: validates the thread with `thread/read`, stores status `linked`, and closes the temporary connection. Linking requires no resume.
- `GET /{pid}/codex/sessions/{sid}/history?cursor=...&limit=3`: returns `{turns:[{id,status,messages:[{role,text}]}],next_cursor}` from bounded App Server history pages. The limit is 1–5 (API default 5; the UI uses 3). Only user and assistant display text is returned; tool output is excluded.
- `POST /{pid}/codex/history/index`: refreshes the local text index of finished turns across linked sessions and returns `{indexed_at,indexed_turns}`. This does not request AI generation. A failed refresh preserves the previous index.
- `GET /{pid}/codex/history/search?q=...&offset=0`: searches the index and returns `{results,next_offset,indexed_at,indexed_turns}`. Results have `session_id`, `session_title`, `turn_id`, and a plain-text `snippet`.
- `GET /{pid}/codex/sessions/{sid}/history/turn?turn_id=...`: opens an exact indexed turn, returning `{id,status,messages:[{role,text}]}`. Returns 404 for turns absent from the project's index.
- `POST /{pid}/codex/sessions/{sid}/resume`: explicitly attempts to take ownership for later read-only discussion. Linked sessions reject tasks and decision delivery until this succeeds.

Protocol/API absence is reported as a limitation rather than emulated success. API responses must not include tokens, environment dumps, or raw global config.
