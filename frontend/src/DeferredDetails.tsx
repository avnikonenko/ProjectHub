import { useEffect, useState, type ReactNode, type Ref } from 'react';
import { nextDisclosureState, type DisclosureState } from './disclosureState';

/** Closed disclosures retain no rendered content; their source records stay intact. */
export function DeferredDetails({ summary, children, openKey, className, id, ref }: {
  summary: ReactNode; children: () => ReactNode; openKey?: string;
  className?: string; id?: string; ref?: Ref<HTMLDetailsElement>;
}) {
  const [state, setState] = useState<DisclosureState>({ key: openKey, open: !!openKey });
  // Reveal a newly selected descendant during this render, so its parent's
  // focus effect can find the mounted element in the same commit.
  const open = nextDisclosureState(state, openKey).open;
  useEffect(() => {
    setState(previous => nextDisclosureState(previous, openKey));
  }, [openKey]);
  return <details className={className} id={id} ref={ref} open={open}
    onToggle={event => setState({ key: openKey, open: event.currentTarget.open })}>
    {summary}
    {open && children()}
  </details>;
}
