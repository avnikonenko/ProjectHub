export function AIResultHeader({ title, savedAt, previous = false, freshness }: {
  title: string;
  savedAt?: string | null;
  previous?: boolean;
  freshness?: string;
}) {
  return <div className="section-title ai-result-header">
    <div>
      <h2>{title}</h2>
      <small>{savedAt
        ? `${previous ? 'Previous version · Saved' : 'Saved'} ${new Date(savedAt).toLocaleString()}`
        : 'No saved result'}</small>
      {freshness && <small className="ai-result-freshness" role="status">{freshness}</small>}
    </div>
  </div>;
}
