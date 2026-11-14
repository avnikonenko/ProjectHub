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

Discussions are inspected up to 65,536 characters each. Deterministic selection retains
complete relevant paragraphs, numbered workflows and diagrams rather than fixed-length
note prefixes. Manageable discussions fit in full; longer ones contribute up to 6,000
characters of selected passages, within a shared 24,000-character discussion budget.
Confirmed shared turns are deduplicated before budget accounting. Fenced diagrams stay
atomic; passages cut by the inspection boundary are omitted. There is no intermediate
AI summary or additional knowledge store.

Selected excerpts retain exact original spans, available speaker/message boundaries,
explicit gap markers, inspection hashes and omission coverage. The collector cache is
versioned, and changes within inspected text invalidate its selection. The unchanged
40,000-character total source budget leaves space for repository evidence. An explicit
discussion sequence supports a proposed workflow independently of user acceptance,
runtime evidence or precise validation criteria.

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

The Scheme implementation review and validation results (private local reports; not distributed)
are preserved separately from this usage and architecture guide.


## Release validation — 2026-11-14

The Example Research reference excerpt previously stopped at 1,200 characters,
before Preparation and Validation & Reporting. The revised selection includes the
complete 4,809-character discussion and its original input → analysis →
classification → planning → preparation → validation/reporting → output sequence.
The complete selected package contains six sources and 21,599 source-text
characters, within the unchanged 40,000-character limit.

A final actual Codex run on an isolated copy used `gpt-6.1-sol`, medium reasoning
and the existing local account. One request completed in 101.6 seconds with
25,604 reported tokens. Its 27 nodes and 28 edges remain proposed, all 99 graph
citation occurrences resolve, and saved sources exactly match supplied evidence.
Independent review verified the workflow against original passages and the
architecture's supported preparation-to-validation/reporting handoffs. No accepted
Gemmi adoption, implemented integration or scientific success was inferred.

An earlier 92.6-second isolated run (25,515 tokens) restored the workflow but left
an architecture handoff implicit. The final prompt requests supported component
handoffs explicitly; no automatic repair loop was added. Final evidence text and
source metadata match the independently reviewed first selection. These two
requests are real protocol/model execution, distinct from mocked generation tests.

Validation passed: 791 backend tests, 58 frontend tests, production build and
whitespace checks. Focused regressions include the original diagram, relevant
later sections, exact spans and speaker boundaries, source/project isolation,
cache invalidation, shared-turn budget reuse, retained coverage counts, fenced
diagrams and partial read-boundary omissions. Existing deprecation and bundle-size
warnings remain.

Real Firefox checks rendered Architecture and Workflow SVGs, verified all workflow
labels after whitespace normalization, and checked the Dependencies empty state.
Browser interaction issued zero generation requests. All evaluation writes were
confined to private database copies; the normal saved Scheme was not replaced.
Raw results and source-rich artifacts remain outside Git. The source fixture in
`tests/fixtures/Example Research_scheme_workflow.txt` preserves the original
reference diagram without personal filesystem paths.

Limits remain explicit: six recent notes, twelve recent indexed turns and bounded
inspection do not establish complete history coverage. Discussion-based graphs
are conceptual; runtime correctness, precise release thresholds and downstream
compatibility remain unverified. Refresh Scheme explicitly to use the new evidence
selection; old saved diagrams are preserved until that action succeeds.
