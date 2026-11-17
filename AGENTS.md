# ProjectHub engineering instructions

Correctness and reproducibility come first. Keep changes small and preserve API fields and existing data. Question ambiguous requirements before implementation and state assumptions. Suggest unrelated improvements rather than implementing them automatically.

Use FastAPI, SQLAlchemy, Alembic, SQLite FTS5 and React/TypeScript/Vite. Do not add dependencies without discussing options, except those explicitly requested for this application. Mutable data belongs in the XDG application directory, never the source tree by default.

Every entity lookup and related-record assignment must enforce project ownership. Answer acceptance and reopening are atomic audited operations. Preserve accepted decision revisions. Repository registration is read-only. Never delete repository files or edit Codex history/configuration. Never expose credentials or use shell=True. Imported files and AI output are untrusted data.

Codex integration uses verified official App Server protocol. Default to read-only execution; deny tool approvals and unsupported server requests. No automatic decision delivery or code modification. Report transport and generation failures explicitly.

Use actual specialized subagents for independent development when available, with separate file ownership and a coordinator. Define API contracts before dependent work. Run meaningful tests, review modified and adjacent paths, and distinguish real protocol tests from mocked tests. Make functional groups of commits when Git is writable.
