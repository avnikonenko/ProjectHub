import { useEffect, useRef, useState } from 'react';

export function ThemeToggle() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    try {
      const saved = localStorage.getItem('projecthub.theme');
      if (saved === 'light' || saved === 'dark') return saved;
    } catch { /* Storage is optional. */ }
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  });
  const explicitChoice = useRef(false);
  useEffect(() => {
    try {
      explicitChoice.current = ['light', 'dark'].includes(localStorage.getItem('projecthub.theme') ?? '');
    } catch { /* Storage is optional. */ }
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const update = () => {
      if (!explicitChoice.current) setTheme(media.matches ? 'dark' : 'light');
    };
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const title = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
  return (
    <button type="button" className="theme-toggle secondary" aria-label={title} title={title}
      onClick={() => {
        const next = theme === 'dark' ? 'light' : 'dark';
        explicitChoice.current = true;
        setTheme(next);
        try { localStorage.setItem('projecthub.theme', next); } catch { /* Storage is optional. */ }
      }}>
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
        strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {theme === 'dark' ? (
          <>
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" />
          </>
        ) : <path d="M20.8 13A9 9 0 0 1 11 3.2 9 9 0 1 0 20.8 13Z" />}
      </svg>
    </button>
  );
}
