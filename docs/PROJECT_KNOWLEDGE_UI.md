# Project Overview and Knowledge

**Overview** describes the project's purpose, architecture and current work.
**Knowledge Summary & records** explains extracted choices, requirements and
reasons, with links to original evidence. Each view has its own saved result.

## Implementation Choices & Limits

Open **Knowledge → Implementation Choices & Limits** to inspect code behavior
such as caps, filters and fallbacks. Generate the view from the **Generation**
tab. Expand a finding to see saved excerpts for its declaration and use. The view
distinguishes documented behavior from inference, and code defaults from
configuration declarations. Follow the usage evidence to check where a limit
applies. Runtime values can differ.

Inspection supports Python and simple values in JSON, TOML and YAML. ProjectHub
excludes sensitive declarations and leaves repository code untouched. Check
**Coverage** for gaps in the inspection. Search and component filters work on the
saved result. Open **Generation & saved versions** to read older results.

## Use

Open a project and select **Knowledge**. Use project-wide search to find saved
records across views, or **Search knowledge** to narrow the Knowledge list.

- Use **Design status** to filter the records. Open **More filters** for
  **Category** and **Scope**. Type part of a scope name or pick a suggestion.
- Expand a record to see its reasons, exact evidence and related records. Use
  **Show more** in long groups and **Reset filters** to start over.
- Open **Interpretations from the same passage** when several records share the
  same supporting passage. The group keeps the individual records accessible.

Saved excerpts remain readable if their original sources become unavailable.
Open available source links to check the surrounding context. **Coverage**
shows the inputs recorded for the saved result, and **Current source
availability** describes what can be checked now. Read the original evidence
before relying on a choice or claim.

## Generate and use a result

1. Open **Generation** and select **Overview**, **Knowledge**, or
   **Implementation Choices & Limits**.
2. Click **Regenerate selected**.
3. Return to the related view. ProjectHub saves successful results automatically.
   Use the view's version controls to read earlier results.

Generation uses the project's selected Codex account, model, effort and
instructions. It sends selected project evidence to Codex. Browsing or filtering
saved results makes no AI request. A failed or cancelled generation keeps the
previous saved result and successful caches.

**Regenerate all** starts Knowledge, Overview, Q&A and Implementation Choices.
Scheme follows after Knowledge saves successfully. If you generate only Scheme,
it uses the current saved Knowledge when available. See
[AI generation](AI_GENERATION.md) for progress, cancellation and retries.

## AI topic tags

Choose **Generate AI tags** to organize saved Knowledge or Q&A. Tagging runs
separately from report generation and preserves the underlying records and
evidence. If tagging fails or is cancelled, existing tags remain available.

- Type a tag name to filter the list, or use **Browse tags** to see descriptions
  and item counts.
- Combine tags with other filters. Tags can overlap. Counts show membership
  before other filters narrow the list.
- Choose **All items** to clear the tag selection or **Untagged** to find records
  without tags.

For API and implementation details, see the
[contributor reference](KNOWLEDGE_UI_INTERNALS.md).
