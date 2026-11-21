import { Fragment } from 'react';

export type SearchSnippetData = {
  snippet: string;
  snippet_parts?: { text: string; match: boolean }[];
};

export function SearchSnippet({ snippet, snippet_parts: parts }: SearchSnippetData) {
  return <p>{parts?.length ? parts.map(({ text, match }, index) => (
    <Fragment key={index}>{match ? <mark>{text}</mark> : text}</Fragment>
  )) : snippet}</p>;
}
