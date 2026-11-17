import { useId, useState } from 'react';
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
  shared_sessions?: { session_id: number; turn_id: string; session_title?: string }[];
};
export function citedSources(sources: BriefSource[], ids: string[]) {
  const byId = new Map(sources.map((source) => [source.id, source]));
  return [...new Set(ids)].flatMap((id) => {
    const source = byId.get(id);
    return source ? [source] : [];
  });
}

function CitationGroup({ ids, numbers, prefix, sources = [] }: {
  ids: string[]; numbers: Map<string, number>; prefix: string; sources?: BriefSource[];
}) {
  const [expanded, setExpanded] = useState(false);
  const controlId = useId();
  const refs = [...new Set(ids)].filter((id) => numbers.has(id));
  if (!refs.length) return null;
  const byId = new Map(sources.map((source) => [source.id, source]));
  function link(id: string, detailed = false) {
    const number = numbers.get(id)!;
    const target = `${prefix}-source-${number}`;
    return <a className={detailed ? 'citation-source' : 'brief-reference'} key={id}
      href={`#${target}`} aria-label={detailed ? undefined : `Read source ${number}`}
      onClick={(event) => {
        event.preventDefault();
        for (let element = document.getElementById(target); element; element = element.parentElement) {
          if (element instanceof HTMLDetailsElement) element.open = true;
        }
        document.getElementById(target)?.scrollIntoView({ block: 'nearest' });
      }}>
      {detailed ? `[${number}] ${byId.get(id)?.label || 'Source ' + number}` : `[${number}]`}
    </a>;
  }
  if (refs.length === 1) return link(refs[0]);
  return <span className="citation-group">
    <button type="button" className="quiet brief-reference" aria-expanded={expanded}
      aria-controls={controlId} onClick={() => setExpanded((value) => !value)}>
      {refs.length} sources {expanded ? '▴' : '▾'}
    </button>
    {expanded && <span id={controlId} className="citation-list">
      {refs.map((id) => link(id, true))}
    </span>}
  </span>;
}

export function briefReferences(ids: string[], numbers: Map<string, number>, prefix: string, sources?: BriefSource[]) {
  return <CitationGroup ids={ids} numbers={numbers} prefix={prefix} sources={sources} />;
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
            {!!source.shared_sessions?.length && (
              <div>
                <small>Also in{' '}
                  {source.shared_sessions.map((session, index) => (
                    <span key={`${session.session_id}:${session.turn_id}`}>
                      {index > 0 && ', '}
                      <a {...linkOptions} href={historyTurnHref({
                        projectId, sessionId: session.session_id, turnId: session.turn_id,
                      })}>
                        {session.session_title ? `${session.session_title} · #${session.session_id}` : `Session #${session.session_id}`}
                      </a>
                    </span>
                  ))}
                </small>
              </div>
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

