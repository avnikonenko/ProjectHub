# Scheme

Scheme shows how a project is organized and how it works. Opening saved diagrams
and dependencies makes no AI request.

## Views

- **Architecture** shows components and their relationships. Expand
  **Code structure** to browse the inspected repository tree.
- **Workflow** shows stages, inputs, outputs and execution order.
- **Dependencies** shows libraries and tools, declared version constraints and
  their role in the project. Check the linked declaration for details.

Components are labeled **observed**, **proposed**, or **uncertain**. Observed
components have supporting evidence. Their runtime behavior needs separate
verification. Green blocks mark entries, purple blocks mark outputs, and blue
blocks mark other components.

## Generate or refresh

1. Link a repository through **Edit overview**, if available, and relevant
   conversations through **Sources**.
2. Open **Generation**, select **Scheme**, then click **Regenerate selected**.
3. Open **Scheme** and expand **Components and relationships** or **Evidence
   used** to check the supporting excerpts.

Generation sends selected project evidence to Codex using the project's account,
model, effort and instructions. ProjectHub saves successful results and keeps
earlier results under **Scheme details → Previous versions**.

## Saved Knowledge

Scheme can use the current saved Knowledge alongside repository excerpts and
linked conversations. **Scheme details → Saved Knowledge context** shows the
Knowledge version and the selection counts for the saved diagram. Generate
Scheme again to incorporate a newer Knowledge version.

## Dependencies

Scheme inspects common Python, npm and Conda dependency declarations. Declared
constraints describe what a project requests. Check your environment separately
for installed versions. Scheme inspects direct declarations without installing
dependencies or executing project code.

## Freshness and coverage

**No changes detected in inspected sources** describes the inputs selected for
the saved result. **Partial coverage** means inspection or selection left some
material out. If an old index makes freshness unknown, refresh **History**
before regenerating Scheme.

- Open **Coverage → Files used and missed** to see which files contributed excerpts
  and up to 100 skipped paths with reasons. Older results may only have aggregate
  counts. A separately labeled current inspection can show today's file selection.
- Open **Coverage → Detailed coverage → File accounting and scope** for the
  inspection counts and recorded limits. A local file read may supply only an excerpt.
- Open **Coverage → Processing diagnostics** for elapsed time, bytes read, cache
  use, and evidence package size.

Adjust the limit that corresponds to the omission reason you see:

| Omission reason | Setting | Default and alternatives |
|---|---|---|
| Source excerpt budget reached | **Settings → Scheme excerpt budget** | 40,000 characters (default), 60,000, or 80,000 |
| Local read budget reached | **Settings → Advanced Scheme settings → Local file read limit** | 2 MiB (default), 4 MiB, or 8 MiB |
| Final evidence package limit reached | **Settings → Advanced Scheme settings → Final evidence package limit** | 140,000 characters (default), 180,000, or 220,000 |

The local inspection still uses a five-second deadline and a 64 KiB per-file
limit. The final evidence package includes excerpts and metadata, so increasing
the excerpt budget alone may leave some files out. Higher local limits can
increase inspection time. Larger AI inputs can increase token use and generation
time. Saved limits apply on the next inspection, and each Scheme revision keeps
the limits recorded for its generation.

See [AI generation settings](AI_GENERATION.md) and the
[implementation reference](scheme-implementation.md).
