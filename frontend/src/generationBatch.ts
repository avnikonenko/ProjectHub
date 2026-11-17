import { useEffect, useRef } from 'react';

export const projectGenerationKinds = ['brief', 'knowledge', 'scheme', 'qa', 'parameters'] as const;
export type ProjectGenerationKind = typeof projectGenerationKinds[number];
export type GenerationRequestState = 'wait' | 'ready' | 'skip';
export type GenerationRequestTracker = { identity: string; sequence: number };
export type GenerationRequestProps = {
  generationRequest?: number;
  onGenerationRequestHandled?: (started: boolean) => void;
};

/** Claim a UI request before invoking the existing view-specific generation action. */
export function claimGenerationRequest(tracker: GenerationRequestTracker, identity: string,
  sequence: number, state: GenerationRequestState): 'start' | 'skip' | null {
  if (tracker.identity !== identity) { tracker.identity = identity; tracker.sequence = 0; }
  if (!Number.isSafeInteger(sequence) || sequence <= tracker.sequence || sequence <= 0 || state === 'wait') return null;
  tracker.sequence = sequence;
  return state === 'ready' ? 'start' : 'skip';
}

export function useGenerationRequest(identity: string, sequence: number, state: GenerationRequestState,
  generate: () => Promise<void>, handled?: (started: boolean) => void) {
  const tracker = useRef<GenerationRequestTracker>({ identity, sequence: 0 });
  const current = useRef({ generate, handled }); current.current = { generate, handled };
  useEffect(() => {
    const action = claimGenerationRequest(tracker.current, identity, sequence, state);
    if (action === null) return;
    // The normal handler remains the only POST/save/recovery path.
    if (action === 'start') void current.current.generate();
    current.current.handled?.(action === 'start');
  }, [identity, sequence, state]);
}

export function idleGenerationKinds(busy: Partial<Record<ProjectGenerationKind, boolean>>) {
  return projectGenerationKinds.filter(kind => !busy[kind]);
}
