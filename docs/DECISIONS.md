# Engineering decisions

1. **Single process serving:** FastAPI serves the built React interface and API on loopback. Vite is optional during frontend development. This gives one normal startup command and avoids mandatory infrastructure.
2. **Relational provenance.** Project records remain distinct, and audit snapshots preserve their changes. Generated Knowledge retains source links.
3. **Explicit acceptance.** In the legacy API, accepting an answer is one user-initiated transaction. Reopening supersedes its decision and preserves the evidence.
4. **SQLite plus FTS5.** SQLAlchemy and Alembic provide a versioned relational model. FTS5 provides local text search.
5. **Read-only Codex.** The App Server integration checks capabilities and denies tool approvals. Users explicitly confirm legacy decision deliveries.
6. **Managed user data:** XDG data storage, consistent backups, bounded imports and project-specific exports preserve user data without modifying registered repositories.
7. **Delivery provenance.** Stored states distinguish queued requests, acknowledged sends, completed turns, and uncertain outcomes. Context and errors remain available for review.
8. **Minimal dependencies:** The requested stack and focused Markdown/Mermaid support are sufficient. Additional infrastructure or unrelated dependencies require a separate proposal.
