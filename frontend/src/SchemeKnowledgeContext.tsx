function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function count(value: unknown): string {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
    ? String(value) : 'Not recorded';
}
function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() && value.length <= 240 ? value : undefined;
}

// This receives the displayed snapshot's metadata, never the latest Knowledge.
// Older Scheme revisions therefore keep the exact context recorded at generation.
export function SchemeKnowledgeContext({ context }: { context?: unknown }) {
  const metadata = object(context) ? context : undefined;
  const publication = text(metadata?.publication_id);
  const status = metadata?.status;
  return <details className="scheme-knowledge-context">
    <summary>Saved Knowledge context</summary>
    {!metadata ? <p>Knowledge context was not recorded for this saved Scheme.</p>
      : status === 'absent' ? <p>No saved Knowledge was available when this Scheme was generated. Scheme does not require Knowledge.</p>
      : status === 'unavailable' ? <p>Saved Knowledge could not be read for this Scheme. See its saved coverage limitations.</p>
      : status === 'published' ? <>
        <p>Knowledge context recorded with this saved Scheme.</p>
        <dl>
          <div><dt>Knowledge version</dt><dd>{publication ?? 'Not recorded'}</dd></div>
          <div><dt>Items available in this version</dt><dd>{count(metadata.available_items)}</dd></div>
          <div><dt>Items included in Scheme evidence</dt><dd>{count(metadata.included_items)}</dd></div>
          <div><dt>Items omitted from Scheme evidence</dt><dd>{count(metadata.omitted_items)}</dd></div>
        </dl>
      </> : <p>Knowledge context metadata is incomplete for this saved Scheme.</p>}
    <p>Scheme uses saved Knowledge with supporting original evidence. Design choices do not establish implemented functionality. Browsing saved details makes no AI requests.</p>
  </details>;
}
