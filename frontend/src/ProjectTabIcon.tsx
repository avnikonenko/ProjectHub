const paths: Readonly<Record<string, string>> = {
  Generation: 'm13 2-9 12h7l-1 8 10-12h-7l1-8Z',
  Knowledge: 'M12 6v15m0-15C9 4 6 3 3 4v15c3-1 6 0 9 2 3-2 6-3 9-2V4c-3-1-6 0-9 2Z',
  Scheme: 'M9 3h6v5H9zm-6 13h6v5H3zm12 0h6v5h-6zM12 8v4m-6 4v-4h12v4',
  History: 'M3 12a9 9 0 1 1 2.6 6.4M3 5v7h7m2-5v5l3 2',
  TODO: 'M9 11h10M9 18h10M3 10l1.5 1.5L7 9m-4 8 1.5 1.5L7 16M4 4h15',
  Sources: 'M9.5 14.5a4 4 0 0 0 5.7 0l4.2-4.2a4 4 0 0 0-5.7-5.7l-2.1 2.1m2.9 2.8a4 4 0 0 0-5.7 0l-4.2 4.2a4 4 0 0 0 5.7 5.7l2.1-2.1',
};

export function ProjectTabIcon({ tab }: { tab: string }) {
  if (!Object.hasOwn(paths, tab)) return null;
  return (
    <svg className="project-tab-icon" viewBox="0 0 24 24" width="16" height="16"
      fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" focusable="false">
      <path d={paths[tab]} />
    </svg>
  );
}
