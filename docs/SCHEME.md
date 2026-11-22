# Scheme

Scheme shows a project's components, workflows, and dependencies. Browsing saved
results makes no AI request.

![Example Scheme for Project X showing file import, validation, storage, and reporting](images/projecthub-scheme.png)

## Views

- **Architecture** shows components and relationships. Expand **Code structure**
  for the selected repository tree.
- **Workflow** shows stages, inputs, outputs, and execution order.
- **Dependencies** shows tools, libraries, declared versions, and their roles.
- **Focused Scheme** diagrams a specific part of the project from an instruction you provide.

Component labels indicate **observed**, **proposed**, or **uncertain** status.
An observation has supporting evidence. Verify runtime behavior separately.
Green marks entries, purple marks outputs, and blue marks other components.

## Generate or refresh

1. Use **Edit overview** to link a repository, if available. Link conversations
   through **Sources**.
2. Open **Scheme**, choose a focus, then click **Generate Scheme**. Choose
   **Balanced** for an even view, **Code first** for the current implementation,
   or **Sessions first** for decisions, rationale, and proposed workflows.
3. Open **Scheme** and expand **Components and relationships** or **Evidence
   used** to check the supporting excerpts.

Generation sends selected evidence to the provider chosen in the project's AI settings.
The result records its focus. You can also choose it in **Generate → Scheme**.
When you run Knowledge and Scheme together, Scheme uses that choice after Knowledge saves.
Focus leaves the repository dependency scan independent of the diagrams.
Find earlier results under **Scheme details → Previous versions**.

## Focused Scheme

Open **Scheme → Focused Scheme**, describe the part you want to understand,
then click **Generate focused scheme**. For example, ask how imported
conversations reach search results, including storage and indexing.

The generator prioritizes evidence relevant to your instruction and cites the
sources that support the diagram. The current diagram and its instruction are
saved together. A successful rerun replaces that focused result. To rerun a
focused scheme, use its own button.

## Saved Knowledge

Scheme can draw on saved Knowledge, repository excerpts, and conversations.
Check **Scheme details → Saved Knowledge context** for the version and counts
used. Regenerate Scheme to use newer Knowledge.

## Dependencies

The Dependencies tab scans supported manifests and safe Python source files in
the linked repository. This includes pip requirements and local includes,
`pyproject.toml`, `setup.py`, `setup.cfg`, Pipfile, Conda environment files,
and npm package manifests. Imports without a matching declaration appear as
module names. Each row links to its declaration or import evidence.

The current inventory is available as soon as you open Scheme. Saved versions
retain their own inventory. The model receives a bounded selection for
explanations, while the table includes every dependency the local scan found.
Check **Dependency scan limitations** for incomplete reads or unsupported
syntax. The scan has a 32 MiB total read limit, a 2 MiB per-file limit, and a
ten-second time budget. Check the project's environment for installed versions.

## Freshness and coverage

**No changes detected in inspected sources** applies to the selected inputs.
**Partial coverage** means some material was omitted. Refresh **History** if an
old index makes freshness unknown.

Open **Coverage** for these details.

- **Files used and missed** lists files that supplied excerpts and up to 100
  skipped paths with reasons. Older results may show counts only. When present,
  **Current inspection** shows today's selection separately.
- **Detailed coverage → File accounting and scope** lists inspection counts and
  limits. A local read may supply only an excerpt.
- **Processing diagnostics** shows time, bytes read, cache use, and evidence size.

Match the omission reason to its setting.

| Omission reason | Setting | Default and alternatives |
|---|---|---|
| Source excerpt budget reached | **Settings → Scheme excerpt budget** | 40,000 characters (default), 60,000, or 80,000 |
| Local read budget reached | **Settings → Advanced Scheme settings → Local file read limit** | 2 MiB (default), 4 MiB, or 8 MiB |
| Final evidence package limit reached | **Settings → Advanced Scheme settings → Final evidence package limit** | 140,000 characters (default), 180,000, or 220,000 |

Local inspection has a five-second deadline and a 64 KiB per-file cap. The
final package includes excerpts and metadata and can still limit coverage
after you increase the excerpt budget. Higher read limits may take longer.
Larger AI inputs can use more tokens and time. Changes apply to the next
inspection. Each Scheme revision retains the limits it used.

See [AI generation settings](AI_GENERATION.md) and the
[implementation reference](scheme-implementation.md).
