# Scheme

Use Scheme to see how a project is organized and how it works. Saved diagrams and
dependency information load without an AI request.

## Views

- **Architecture:** components and their relationships. Expand Code structure to
  browse the inspected repository tree. Diagram labels emphasize project
  capabilities; module and command names belong in component details.
- **Workflow:** processing stages, inputs, outputs, and execution order.
- **Dependencies:** declared libraries and tools, version constraints, categories,
  and short project-specific explanations.

Components are labeled observed, proposed, or uncertain according to their sources.
A view can be empty when the available information does not support a diagram.
Green blocks mark entries, purple blocks mark outputs, and blue blocks mark
intermediate components. Colors follow connections, not list order; isolated
components and cycles use blue. Evidence-status labels and border styles remain.

## Generate or refresh

1. Link a repository through **Edit overview**, if available, and relevant
   conversations through **Sources**.
2. In **Generation results**, select **Scheme** and click **Regenerate selected**.
3. Open the **Scheme** tab to inspect the diagrams and expand **Evidence used** for original excerpts, or
   **Components and relationships** for individual references.

Generation uses the project's selected Codex account, model, reasoning effort,
and AI instructions. Selected project evidence is sent through Codex only when you
request generation. Discussion-based designs can appear as proposed components;
a linked repository is needed for code structure and manifest inspection.

Saved results remain readable when Codex is unavailable. Earlier generated results
are available through **Previous versions**.

## Saved Knowledge

Scheme can use the project's current saved Knowledge alongside repository inspection
and linked conversations. Knowledge is optional.

A bounded selection preserves design status, scope, rationale, conflicts and
supersession, with supporting original passages kept separately. Accepted design
does not establish implemented or tested functionality. Some evidence may be
historical or unavailable; the saved interpretation retains that limitation.
Knowledge helps choose relevant capabilities, while original passages and
repository evidence support the diagram's claims.

Expand **Scheme details → Saved Knowledge context** to inspect the version
and included/omitted item counts recorded with that Scheme. Selection uses at most
12,000 text characters and never modifies Knowledge. Large snapshots are queried
through bounded database projections, rather than loaded wholesale into the AI
input. Items with many references may use a disclosed selection of original
passages; omitted references do not independently support generated claims.

Saving a new Knowledge version updates the existing Scheme's freshness indicator,
even if selected statements are unchanged. Select **Scheme** in
**Generation results** and click **Regenerate selected** to use the new context.
Older Scheme revisions retain the Knowledge version they used. Reading and selecting
Knowledge adds no model calls; Scheme still makes one explicit generation request.

## Dependencies

Inspection supports `pyproject.toml` project dependencies and dependency groups,
common Python requirements files, `package.json`, and Conda `env`/`environment`
YAML manifests, including CPU/GPU variants and nested pip declarations.
Environment declarations, optional, development, build and documentation
dependencies retain separate categories and source references. Unsupported YAML
constructs are omitted with a coverage limitation; this is not a general YAML
interpreter.

Versions are declared constraints, not confirmation of what is installed. Scheme
does not install packages, analyze transitive dependencies or lockfiles, or execute
project code. AI explanations describe dependencies found by inspection.

## Freshness and coverage

**No changes detected in inspected sources** means those inputs still match the
saved result. **Partial coverage** means some material was excluded, unsupported,
or beyond inspection limits. An unavailable repository or stale conversation index
can make freshness unknown. Refresh **History** separately to update indexed messages.

Scheme uses selected repository excerpts, recent indexed conversations, and notes;
it does not read every file or the entire project history. Check **Evidence used**
to see what supported the result. Diagrams explain available evidence and can miss
connections or describe plans rather than working functionality.

Inspection prioritizes executable modules declared by literal Python entrypoints
in `setup.py` and `pyproject.toml`, and retains README feature sections instead of
only introductory badges. Repository excerpts receive a bounded share of the
input budget alongside discussions. Setup files are parsed statically and never
executed; dynamic declarations and targets outside inspected paths may be omitted.
Project guides under `docs` and `documentation` contribute their original purpose
and section outline, including short command-help descriptions. Guides mentioning
declared commands take priority over peripheral documentation. Backup and archive
paths do not displace current application evidence, but remain in the inventory.

Source inspection uses a 2 MiB read budget and a cooperative 5-second deadline,
rather than a fixed number of files. Individual source reads remain limited to
64 KiB, and the evidence sent to AI has separate text and serialized-size limits.
Code structure displays at most 300 paths; that display limit does not restrict
which files can be inspected. Coverage reports distinguish budget-stopped reading
from inspected material omitted from the final AI input.

Expand **Code structure → How ProjectHub inspected this repository** for saved
counts: Git-listed paths, exclusions, eligible candidates, attempted and successful
reads, prefix-only reads, and files represented in the final AI evidence package.
The same disclosure records the limits and selection trade-offs. Counts describe
processing coverage, not the percentage of project functionality understood.
Ignored untracked files are outside the Git listing and are not counted.

Counts belong to the saved generation and remain attached to its revisions.
At the bottom of Scheme, **Coverage & technical details → Skipped files**
lists up to 100 eligible paths left out of the AI input and the reason for each.
Earlier generations may only record aggregate reasons. A file can contribute
multiple excerpts, so aggregate omission counts can differ from file counts.
Older Schemes explicitly report that these counts were not recorded; a separately
labeled current inspection can be viewed without generating a new Scheme.
**Scheme details → Repository inspection diagnostics** shows recorded timing,
bytes read, cache use and package size. Tool details stay collapsed, keeping the
diagram focused on the project.

Manual diagram editing and export are not currently available.

See [installation](../README.md#install-and-run), [AI generation settings](AI_GENERATION.md),
and the [implementation reference](scheme-implementation.md) for further details.
