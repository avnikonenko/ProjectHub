# Security boundaries

ProjectHub is intended for one trusted user on a local machine. It has no application
login. Keep it bound to loopback; public hosting, reverse proxies and multi-user use
require authentication and a separate security review.

## Data and permissions

- Mutable data and configuration use XDG directories outside the checkout. Back up
  the application data directory before upgrades. Keep credentials out of Git.
- Entity lookups and related-record assignments must enforce project ownership.
  Imports, repository content, conversation text and AI output are untrusted data.
- Repository registration and inspection are read-only. Git commands use argument
  arrays and timeouts. Inspection does not run project tests or package installers.
- Imported text/Markdown is size-limited. Markdown renders without raw HTML or remote
  images. Mermaid uses strict rendering in a sandboxed frame.
- Loopback Host/Origin checks reduce browser-origin risks; they are not authentication.
  Local processes and other software running as your user remain inside the trust boundary.

## Codex

Codex manages its own ChatGPT credentials. ProjectHub uses the existing App Server
integration, checks authentication type, and requests read-only analysis without tool
approvals. Unsupported server requests fail closed. Inherited MCP connectors are
refused for analysis; tool paths are restricted through process overrides.

AI generation sends selected context to OpenAI only when requested. Do not send
information you are not permitted to share. Queued or interrupted requests are not
proof of completed delivery. Generated claims require review; citations establish
source attribution rather than correctness.

## Release checks and limitations

Run the [validation checks](TEST_REPORT.md) against the release commit. Mocked protocol
tests do not prove effective sandbox permissions or real provider behavior; verify
those separately with the installed Codex version. Browser and dependency security
checks also need current execution, not an old report.

Backup creation has a residual same-user directory-replacement race: a malicious
process with your filesystem permissions could substitute a writable directory.
Private directories reduce exposure but do not isolate hostile same-user processes.

This guide describes intended boundaries and known limits, not an independent
security certification or a completed vulnerability audit.
