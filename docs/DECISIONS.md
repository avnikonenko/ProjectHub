# Engineering decisions

1. **Single process serving:** FastAPI serves the built React interface and API on loopback. Vite is optional during frontend development. This gives one normal startup command and avoids mandatory infrastructure.
2. **Relational provenance:** Questions, answers, decisions and tasks are distinct records. Audit snapshots preserve revisions. Deterministic views derive from committed data rather than an independently mutable AI summary.
3. **Explicit acceptance:** Accepting an answer is atomic and user initiated. Reopening supersedes its decision rather than deleting evidence. AI proposals never silently become accepted knowledge.
4. **SQLite plus FTS5:** SQLAlchemy and Alembic provide a versioned relational model; SQLite FTS5 provides local text search. No vector database is needed for the MVP.
5. **Read-only Codex first:** Official App Server integration is capability verified. Permission requests are denied in this MVP. Explicit preview and send are separate from decision acceptance.
6. **Managed user data:** XDG data storage, consistent backups, bounded imports and project-specific exports preserve user data without modifying registered repositories.
7. **No hidden delivery claim:** Queued, submitted, completed and failed states are separate, with exact context snapshots and retained errors.
8. **Minimal dependencies:** The requested stack and focused Markdown/Mermaid support are sufficient. Additional infrastructure or unrelated dependencies require a separate proposal.
