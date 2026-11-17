# Project Overview and Knowledge

Matching direct-user choices supported by the same exact passage may appear under
an expandable **Interpretations from the same passage** group. The grouping is a
presentation aid, not a semantic merge: all records, statuses, evidence and filter
counts remain available. Numbered proposal acceptances, different scopes and
conflicting or stale records remain separate. Direct links reveal related records.
Context-review notes are shown separately from historical design status.

Project Overview shows the general project picture. Knowledge Summary provides a
more detailed perspective on extracted choices, requirements, reasons and supporting
passages, with searchable Knowledge records alongside it. The saved perspectives
are independent. Scheme provides diagrams in a separate tab.

## Implementation Choices & Limits

Under **Knowledge**, switch from **Knowledge Summary & records** to
**Implementation Choices & Limits**. This independent view explains consequential
code behavior: inspection caps, filters, fallbacks, unsupported paths and processing
choices. It is not a catalogue of constants. Generate or refresh explicitly; browsing, filtering
and inspecting saved entries makes no model calls.

Static inspection supports Python and simple scalar JSON, TOML and YAML
configuration. It never imports or executes repository code. Credentials and
sensitive declarations are excluded; supported formats and inspection limits are
reported in coverage. Unsupported languages and syntax may leave gaps.

Each finding shows its behavior and concrete consequence, with separate clickable
locations for the declaration and the code that applies or references it. Links open
the saved evidence excerpt, not an unverified current file. Inspected content hashes
identify the saved file version. A constant declaration alone does not establish
enforcement; unsupported connections are not invented.

Entries preserve their name, declared value/default when applicable, component, file,
symbol and line range. Configuration values are labeled
separately from code defaults; precedence is not assumed. Conditional declarations
remain separate with their unevaluated conditions. Required parameters and missing
defaults are labeled explicitly; expressions such as CPU-derived values remain
expressions rather than invented numbers.

AI explains behavior and concrete project-specific trade-offs using the inspected
evidence. Findings require supporting usage evidence. Documented and inferred explanations remain distinct. Reasons for choosing
a value are included only when documented; generic methodological caveats and
individual-run/effective values are outside this view. Search and component filters
operate locally. Expand an entry for its saved declaration and usage evidence.
Other inspected defaults remain available in a collapsed section. Existing older
parameter snapshots remain readable; regenerate to discover the new findings.

Results save independently in existing generation revisions. They do not replace
or regenerate Project Overview, Knowledge Summary, Knowledge records or Scheme.
Source changes flag the saved result; failed/cancelled generation preserves the
previous snapshot. **Generation & saved versions** opens earlier parameter results,
whose original coverage is kept separate from the current source check.

Validation of the earlier declaration-only version on 2026-11-17 used an isolated, frozen ProjectHub checkout and real
Codex (`gpt-6.1-sol`, medium reasoning). One request completed in 204.5 seconds:
100 selected declarations retained their inspected defaults, with 96 purposes,
41 trade-offs and one documented rationale; unsupported explanations stayed
unknown. Reported usage was 56,261 input and 9,925 output tokens. This is one
measured run, not a general speed or cost guarantee. Coverage explicitly recorded
103 omitted declarations, unsupported-language files and partial reads.

An isolated Firefox check verified saved values, conditional/expression/required
labels, local search, and source-change warnings without automatic regeneration
or model calls. Automated checks cover project ownership, independent revisions,
source changes, cancellation/failures and explanation-reference validation.

The revised choices view was evaluated with real Codex (`gpt-6.1-sol`, medium)
against an isolated Python repository containing a 24-file cap imported from
another module, a 12-record slice, a `.bak` exclusion, an exception fallback and
an unsupported export. All five behaviors were surfaced with usage references;
an unused worker default stayed a parameter-only record. One request completed
in 34.4 seconds (16,801 input / 1,430 output tokens). This fixture demonstrates
the supported paths, not exhaustive repository analysis. Static inspection of
ProjectHub completed its 63 supported files within the unchanged five-second
budget; selection and evidence-size limits still make the result partial.

## Use

Open a project and select **Knowledge**. Published knowledge loads when that view
opens; returning to it refreshes the read-only snapshot. Use the single project-wide
search bar to find saved knowledge and its evidence alongside other project records.
Within Knowledge, text search includes the original scope. Use status, category, and
tag filters together with the exact Scope filter under **Advanced filters**. An active
scope is shown even when that section is collapsed. **Reset filters** clears all filters. Records group
by design status and expand to show reasons, exact evidence, recorded relationships,
and current source availability. Show more exposes every record in large groups.

Unavailable evidence does not change historical agreement. Saved excerpts remain
visible; unavailable source links are disabled. Excerpts render as plain text, so
source HTML and scripts cannot execute. Current source coverage is separate from
the coverage recorded at generation.

A legacy Overview generated by the knowledge pipeline shows **Important choices** only
when its publication and project match the retrieved snapshot. The deterministic
selection uses supported, nonsuperseded, nonconflicting AGREED records first, then
up to two UNRESOLVED records, with a total limit of five. Stable item IDs break
ties; this does not claim scientific importance or cross-session chronology.
Exact repeated statements with identical scope/status are suppressed in highlights
only. The Knowledge page retains all records.

Choice links use `#knowledge?project=ID&publication=PUBLICATION&item=ITEM_ID`.
A publication mismatch blocks substitution and offers an explicit current-snapshot
view. Missing item IDs are reported. Historical knowledge is not silently replaced
with the latest snapshot, and unrelated knowledge is never attached to legacy briefs.
An unavailable project link displays an error rather than another project's data.

## Generate and use a result

Saved knowledge loads automatically when Knowledge opens. If no active snapshot
exists, the saved generated result appears directly, with its brief and all records.
There is no separate reload button or collapsed preview workflow.

Click **Refresh Knowledge Summary** to generate, validate and save using the
configured project account, model, effort and instructions. The normal action
sends `confirmed:true,publish:true`; browsing never invokes AI. No separate
Save/Use step is needed. The server checks source and repository freshness before
atomically saving the records, matching summary and revision. Failure or
cancellation keeps the previous saved result and completed extraction caches.

**Regenerate all** runs Knowledge alongside Overview, Q&A and Implementation
Choices, then starts Scheme only after Knowledge saves successfully. The server
owns this follow-up, so changing the selected project does not discard it.
Knowledge failure/cancellation skips Scheme. Single-view Scheme generation still
uses the currently saved Knowledge. Earlier unsaved previews remain available in
a collapsed historical section, with the legacy source and publication guards.

The Overview tab has its own **Refresh Project Overview** action. Its general
summary and revision history are separate from Knowledge Summary. Older detailed
summaries can be recovered from a matching saved publication/revision without
rewriting the general Overview. Before replacing that Overview or deleting a
backing revision, the legacy summary is copied into the existing Knowledge snapshot
in the same transaction, preserving its publication identity and tags.
Knowledge Summary appears on the Knowledge tab;
an unpublished preview is labeled separately from the saved summary.

Successful generated results remain in existing project-scoped revisions and
recover after refresh/restart. Returning to Knowledge refreshes saved results.
Project switching aborts browser requests, not backend work, and late responses
cannot replace another project's state. Original evidence and all recorded design
statuses remain unchanged by activation.

The project-wide search indexes active saved project knowledge, while an unused
generated candidate remains inspectable through its own records and filters.

## Source availability and limits

Saved excerpts remain readable if an original source becomes unavailable. Source
links open the stored passage when navigation is supported. Older publications
remain distinct from the current snapshot; a mismatched link does not silently
show a different result.

Knowledge filters match recorded categories, statuses, and scopes. Search uses
saved text rather than inferring equivalent meanings. AI interpretations can miss
or misread information; expand the supporting evidence when a choice matters.

For API details and interface architecture, see the
[contributor reference](KNOWLEDGE_UI_INTERNALS.md).

## AI topic tags

Knowledge and AI Q&A can organize results with project-specific **AI tags**.
An item can appear under several tags without being duplicated or merged.

- Type in **AI tags** to filter by matching tag names.
- Open **Browse tags** to see descriptions and item counts, then select a tag.
  Knowledge also shows counts by design status.
- Choose **All items** to clear tag selection, or **Untagged** to find items without tags.
- Combine tag selection with the view's other filters. Counts describe tag membership;
  additional filters can reduce the visible results.

Choose **Generate AI tags** to organize a saved Knowledge result or current Q&A.
Knowledge, Overview and Q&A generation do not wait for tagging. Older saved results
remain usable without tags. Tagging has its own progress, cancellation and time budget;
failed or cancelled tagging preserves the existing tags and completed batch caches. Browsing or filtering tags makes no AI request.

Tags help navigation; they do not change the underlying claims, decisions, evidence,
or source references. Similar tags may overlap. A project catalog supports up to 400 topics (up to 40 new topics per model reply);
a generation that exceeds it fails without replacing the previous saved result.
