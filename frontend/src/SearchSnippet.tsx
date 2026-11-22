import { Fragment } from 'react';

export type SearchTextPart = { text: string; match: boolean };

export type SearchSnippetData = {
  snippet: string;
  snippet_parts?: SearchTextPart[];
};

export function SearchText({ text, parts }: { text: string; parts?: SearchTextPart[] }) {
  return parts?.length ? parts.map(({ text: value, match }, index) => (
    <Fragment key={index}>{match ? <mark>{value}</mark> : value}</Fragment>
  )) : text;
}

export function SearchSnippet({ snippet, snippet_parts: parts }: SearchSnippetData) {
  return <p><SearchText text={snippet} parts={parts} /></p>;
}
