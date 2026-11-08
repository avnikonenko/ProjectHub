import { chatGPTTranscriptHref, historyTurnHref } from './historyLinks';

export type BriefSource = {
  id: string;
  label: string;
  text: string;
  session_id?: number;
  turn_id?: string;
  decision_id?: number;
  note_id?: number;
  share?: boolean;
  kind?: string;
};
export function citedSources(sources: BriefSource[], ids: string[]) {
  const byId = new Map(sources.map((source) => [source.id, source]));
  return [...new Set(ids)].flatMap((id) => {
    const source = byId.get(id);
    return source ? [source] : [];
  });
}

export function briefReferences(ids: string[], numbers: Map<string, number>, prefix: string) {
  return [...new Set(ids)].map((id) => {
    const number = numbers.get(id);
    if (!number) return null;
    const target = `${prefix}-source-${number}`;
    return (
      <a
        className="brief-reference"
        key={id}
        href={`#${target}`}
        aria-label={`Read source ${number}`}
        onClick={() => document.getElementById(target)?.setAttribute('open', '')}
      >
        [{number}]
      </a>
    );
  });
}

export function BriefSources({ sources, prefix, openInNewTab = false }: {
  sources: BriefSource[];
  prefix: string;
  openInNewTab?: boolean;
}) {
  const projectId = Number(prefix.split('-')[0]);
  const linkOptions = openInNewTab ? { target: '_blank', rel: 'noopener' } : {};
  return (
    <ol>
      {sources.map((source, index) => (
        <li key={source.id}>
          <details id={`${prefix}-source-${index + 1}`}>
            <summary>
              {source.turn_id ? `Session #${source.session_id} · Turn ${source.turn_id.slice(0, 8)}…`
                : source.note_id !== undefined
                  ? source.label || `${source.kind === 'ai_note' ? 'AI note' : 'Note'} #${source.note_id}`
                  : source.label}
            </summary>
            {source.turn_id && <small>{source.label}</small>}
            <pre className="json-report">{source.text}</pre>
            {source.session_id !== undefined && source.turn_id && (
              <a {...linkOptions} href={historyTurnHref({ projectId, sessionId: source.session_id, turnId: source.turn_id })}>
                Read full turn
              </a>
            )}
            {source.share && source.note_id !== undefined && (
              <a {...linkOptions} href={chatGPTTranscriptHref({ projectId, noteId: source.note_id })}>Read full transcript</a>
            )}
          </details>
        </li>
      ))}
    </ol>
  );
}

