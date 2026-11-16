/** Workspace-local identities prevent delayed loads and old forms changing a newer editor. */
export function createEditorIdentity() {
  let latest = 0;
  return {
    begin: () => ++latest,
    current: (identity: number) => identity === latest,
    invalidate: () => { latest++; },
  };
}
