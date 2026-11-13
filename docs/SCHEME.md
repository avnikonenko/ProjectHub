# Scheme

Scheme has three views: Architecture, Workflow, and Dependencies. Code structure is
collapsed beneath Architecture. Opening a view never invokes AI.

## Use

Link a repository through Edit overview and relevant conversations through Sources.
Open Scheme to inspect dependency declarations or load saved diagrams. Generate / Refresh
Scheme sends selected evidence to the project's existing configured Codex account, using
the same model, reasoning effort, and project instructions as other AI features.

Architecture describes components; Workflow describes operations and execution order.
Observed, proposed, and uncertain indicate evidence support, not runtime correctness.
Each graph may be empty independently when evidence is insufficient. Dependencies show
declared constraints, not installed versions. Expand Evidence used for original excerpts
and provenance, or Components and relationships for individual citations. Previous versions
use the existing generation revision history.

## Architecture and storage

The pipeline is read-only inspection, preserved evidence, one Codex structured-output
request, graph/reference validation, deterministic Mermaid generation, and atomic
snapshot/revision persistence. Migration 0019 adds project_schemes, storing one current
snapshot per project. Existing brief/map fields and historical reports are preserved.

Each snapshot contains graphs, Mermaid, direct dependencies, AI purpose explanations,
directory structure, sources with hashes/provenance, coverage, source fingerprints,
generation settings, and lightweight diagnostics. Evidence snapshots preserve the bounded
excerpts supplied to Codex, not complete repository/history exports.

## Evidence and performance

Inspection reuses existing Git safety, path, and exclusion helpers. Defaults bound the tree
to 300 safe paths, content inspection to 24 files of up to 64 KiB each, direct dependency
declarations to 100, and total evidence text to 40,000 characters. Production entrypoints and their explicit local imports are prioritized over utility scripts,
tests, and migrations. Large source files contribute at most a 64 KiB prefix with partial
coverage recorded. Code excerpts balance declarations, imports, and syntactic call names
without arguments, initializer values, or defaults; private dependency and
import locations are redacted. Project code is never executed.

Supported declarations: PEP 621 pyproject.toml, Python dependency groups, common requirements
text files with bounded safe includes, and package.json. Optional/development categories
remain separate. Lockfiles, transitive dependency analysis, installation, and assumed
import-to-distribution mappings are not supported.

Retrieval reads at most 12 recent stored indexed conversation excerpts and six notes, plus
bounded metadata, saved architecture, and previous brief context. It never invokes the
full-history AI summarization pipeline. Sources and omissions are recorded. Unchanged
extraction is reused after safe file-stat and bounded stored-record checks.

Generation makes one Codex request for both graphs and dependency explanations, with no
automatic repair loop. Each graph is limited to 20 nodes and 40 edges. Dependency names
and constraints remain deterministic; AI can explain only known dependency IDs.
Conversation-only proposed tools can appear as proposed graph components.

## Freshness and coverage

These are separate indicators. No changes detected in inspected sources compares input
fingerprints; it does not establish coverage or AI correctness. Partial coverage records
bounded, excluded, or unsupported material. Unavailable repositories and stale conversation
indexes make freshness unknown. Refresh History separately to update indexed conversations.
Saved schemes remain readable when Codex is unavailable.

## Security and limitations

Inspection never installs packages, alters Codex profiles/history, or enables generation
tools. It rejects unsafe paths/symlinks and honors evidence exclusions and Git ignore rules.
Reference validation rejects unknown IDs/endpoints and ungrounded repository paths. Mermaid
is generated from validated graphs, rendered in strict mode with text labels, and stripped
of active content and navigation.

Static inspection does not establish runtime call relationships, successful integrations,
passing tests, or scientific validity. A valid citation identifies supplied material; it
does not prove every generated statement. Workflow reconstruction can be incomplete.
No external code-intelligence engine is required. Manual overrides and export are deferred.

## Running

Use the README's existing install/start commands; startup applies migration 0019. Back up
the application data directory before upgrading. No new dependencies or API key are needed.

## Historical reports

The [Scheme implementation review and validation results](experiments/TEST_REPORT.md#scheme-implementation-review)
are preserved separately from this usage and architecture guide.
