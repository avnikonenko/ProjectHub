# ProjectHub security review

**Historical review:** the dated findings below describe the initial implementation, not current validation status. Subsequent HTTP, database, frontend build, and targeted real App Server checks are summarized in [historical validation results](experiments/TEST_REPORT.md). This is not an independent release security certification.

Date: 2026-11-05. Reviewer: independent QA agent, with coordinator integration review. Scope: new backend, database/migration, frontend, startup scripts and Codex integration. Assumption: one trusted local OS user, loopback binding, private application data directory. This is a code and targeted-test review; full HTTP/browser and real Codex sandbox validation remain blocked as described in [historical validation results](experiments/TEST_REPORT.md).

## Controls present in source

- Startup binds to `127.0.0.1`. Host and Origin middleware rejects unrecognized hosts and cross-origin mutations; development origin port 5173 is explicitly trusted. A 3 MiB request limit is applied before parsing mutation requests. These middleware controls still need the authored HTTP tests to run.
- Every core entity lookup and foreign-record assignment is project scoped. Codex session IDs, deliveries and events are checked against their project. A Codex thread cannot be associated with multiple projects. Database/context tests verify relevant portions; HTTP isolation remains unverified.
- Answer acceptance/reopening acquire a SQLite write lock and preserve audit snapshots, original answers and superseded decisions. No endpoint deletes registered repository files. Accepted decision revisions retain before/after history.
- Existing repositories are inspected with argument-list Git invocation, without `shell=True`; traversal and symlinked paths are rejected. No repository write is part of registration.
- Imports are UTF-8 `.md`/`.txt` only, limited to 2 MiB, with safe display names and generated storage names. Directory descriptors, `O_NOFOLLOW`, exclusive creation and private file modes protect the managed upload write. Imported contents are not executed.
- Application data uses XDG storage by default. Database paths and ancestor symlinks are rejected at initialization; the database is mode 0600. Upload/backup files are private. SQLite backup uses its consistent backup API.
- React renders text and Markdown without raw HTML. Remote Markdown images are suppressed. Mermaid uses strict mode and its result is displayed in a sandboxed iframe with restrictive inline-document CSP. Browser testing remains required.
- App Server communication is private stdio. Threads and turns request read-only filesystem access, restricted networking and no permission approvals. Unsupported server requests fail closed. Shell/browser/apps/plugins/hooks are disabled through process-level overrides; nonempty inherited MCP connector inventory prevents a turn.
- Subscription-only integration now forces the OpenAI provider, removes known API-key/base-URL overrides from subprocess environment, and checks `account/read` reports ChatGPT authentication before thread operations. Account details are not exposed or persisted. Fake-protocol/database tests cover refusal paths; real provider/auth behavior remains unverified.
- Decision acceptance never sends an update. Delivery requires a distinct preview and explicit confirmation. Changed decision previews are rejected. Busy/disconnected sessions queue locally. Timed-out submissions are marked unverified and not silently retried; restart recovery preserves uncertainty.
- Context packages contain a bounded, stable selection of accepted decisions/open questions/incomplete tasks, carry SHA-256 versions, and exclude other projects. AI output is saved only as proposed notes after an explicit action.

## Findings addressed during implementation

1. **High: authentication mode did not establish the selected billing/provider path.** A ChatGPT CLI login alone could coexist with a configured API provider. Fixed with provider overrides, account-type verification and known credential-environment filtering. Added mocked refusal coverage. Real integration verification remains mandatory.
2. **Data consistency: simultaneous answer acceptance could produce conflicting current decisions.** Addressed with `BEGIN IMMEDIATE` before state inspection; a concurrency HTTP test exists but has not run.
3. **Input validation: generated PATCH schemas dropped create-field constraints.** Addressed by preserving copied field metadata. Independent Pydantic tests pass.
4. **Filesystem safety: upload directory replacement could redirect writes.** Addressed using a directory descriptor and exclusive no-follow file creation. Real HTTP adversarial testing remains pending.
5. **Recovery: pending deliveries could be mistaken for verified completion.** App startup now eagerly recovers running/sending records to interrupted/unverified; database tests pass. Event ordering and completion races have mocked coverage.

## Residual findings and optional hardening

**Low, same-UID filesystem race in backups.** Backup creation checks the directory then opens/reopens paths by name. A malicious process running as the same user could replace a writable managed directory between those operations. This is not an Internet or cross-project authorization bypass under the stated private-directory model, but it is weaker than the upload path.

Smallest proposed change: acquire the backup directory with `O_DIRECTORY | O_NOFOLLOW`, create the target relative to that descriptor, and design the SQLite destination connection so it cannot reopen a substituted path (for example a carefully tested descriptor-backed destination on Linux). Preserve private modes and remove failed partial backups. Tradeoff: descriptor-backed SQLite/WAL/journal semantics require validation; an untested path trick should not be introduced. Validate with deterministic directory-replacement/symlink tests and restore a real resulting backup. This hardening is suggested, not silently implemented.

**Local trust boundaries.** No application login is implemented. Other processes running as the same user can normally read private application files and invoke loopback APIs; port 5173 is trusted for development. Network exposure, reverse proxying or multi-user deployment requires authentication and a new review. Host `testserver` is allowed for the HTTP test client and should be gated if the service's host allowlist is hardened for other deployments.

**Dependency reproducibility and auditing.** Python uses bounded version ranges, and network restrictions prevent a complete frontend dependency lock/build and vulnerability audit. Capture validated dependency locks after successful installation. Do not treat unavailable vulnerability tools as a clean audit.

**Model/tool isolation is unverified in the real environment.** Configuration flags and sandbox policies are version-sensitive. Before using private research context, run the real handshake/read-only discussion test and confirm tool inventories and effective permissions. A read-only filesystem policy alone cannot prove absence of external side effects; the integration therefore refuses inherited MCP connectors and disables other tool paths.

## Conclusion

The design keeps knowledge acceptance, AI proposals and execution separate and preserves project provenance. No unresolved critical defect was established by this source review after the listed fixes. This is not a production security approval: HTTP/browser tests, dependency checks and a real App Server sandbox/authentication smoke test remain outstanding.
