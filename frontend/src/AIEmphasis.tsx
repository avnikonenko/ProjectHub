import { Fragment, type ReactNode } from 'react';

/** Render bold only; generated prose stays text, with no HTML or block markup. */
export function AIEmphasis({ text, renderText = value => value }: {
  text: string;
  renderText?: (text: string) => ReactNode;
}) {
  return text.split(/(?<![\\*])\*\*([^*\r\n]+)\*\*(?!\*)/g).map((part, index) =>
    index % 2
      ? <strong className="ai-emphasis" key={index}>{renderText(part)}</strong>
      : <Fragment key={index}>{renderText(part)}</Fragment>);
}
