import { useId } from 'react';
import { ALL_TOPICS, OTHER_TOPICS, matchingTopicChoices, type TopicChoice } from './topicGroups';

export function TopicPicker<T>({ choices, selected, onSelect, search = '', onSearch, ariaLabel = 'Tags' }: {
  choices: TopicChoice<T>[]; selected: string; onSelect: (id: string) => void;
  search?: string; onSearch?: (query: string) => void; ariaLabel?: string;
}) {
  const inputId = useId();
  const matches = matchingTopicChoices(choices, search);
  function choose(id: string) { onSearch?.(''); onSelect(id); }
  return <div className="topic-picker" aria-label={ariaLabel}>
    <label htmlFor={inputId}>AI tags</label>
    <div className="topic-control">
    <input id={inputId} type="search" aria-description={`Selected tag: ${choices.find(group => group.id === selected)?.label ?? 'All items'}`} placeholder={selected === ALL_TOPICS ? 'Filter tags…' : choices.find(group => group.id === selected)?.label ?? 'Filter tags…'} value={search}
      onChange={event => { onSelect(ALL_TOPICS); onSearch?.(event.target.value); }} />
    <details className="topic-list">
      <summary aria-label="Browse tags" title="Browse tags">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
      </summary>
      <div className="topic-options">
      <p className="muted">AI-generated tags may overlap.</p>
      {choices.length <= 2 && <p>No AI tags saved for this version. Tags are generated with the next Knowledge or Q&A generation.</p>}
      <ul>{choices.filter(group => group.id === ALL_TOPICS || group.id === OTHER_TOPICS || matches.includes(group)).map(group => <li key={group.id}>
        <button type="button" className="quiet" aria-pressed={!search && selected === group.id}
          onClick={() => choose(group.id)}>{group.label} ({group.items.length})</button>
        <p>{group.description}</p>
        {!!Object.keys(group.statuses).length && <small>{Object.entries(group.statuses)
          .map(([name, count]) => `${name.replaceAll('_', ' ')}: ${count}`).join(' · ')}</small>}
      </li>)}</ul>
      {search && !matches.length && <p>No matching tags.</p>}
      </div>
    </details>
    </div>
  </div>;
}
