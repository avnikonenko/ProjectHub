export type DisclosureState = { key?: string; open: boolean };

/** A new selection reveals its record; clearing selection forgets the old key. */
export function nextDisclosureState(state: DisclosureState, key?: string): DisclosureState {
  if (state.key === key) return state;
  return { key, open: key ? true : state.open };
}
