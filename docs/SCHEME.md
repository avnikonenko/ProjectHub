# Scheme

Use Scheme to see how a project is organized and how it works. Saved diagrams and
dependency information load without an AI request.

## Views

- **Architecture:** components and their relationships. Expand Code structure to
  browse the inspected repository tree.
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
2. Open **Scheme** and click **Generate / Refresh Scheme**.
3. Inspect the diagrams and expand **Evidence used** for original excerpts, or
   **Components and relationships** for individual references.

Generation uses the project's selected Codex account, model, reasoning effort,
and AI instructions. Selected project evidence is sent through Codex only when you
request generation. Discussion-based designs can appear as proposed components;
a linked repository is needed for code structure and manifest inspection.

Saved results remain readable when Codex is unavailable. Earlier generated results
are available through **Previous versions**.

## Dependencies

Inspection supports `pyproject.toml` project dependencies and dependency groups,
common Python requirements files, and `package.json`. Optional and development
dependencies remain separate.

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

Manual diagram editing and export are not currently available.

See [installation](../README.md#install-and-run), [AI generation settings](AI_GENERATION.md),
and the [implementation reference](scheme-implementation.md) for further details.
