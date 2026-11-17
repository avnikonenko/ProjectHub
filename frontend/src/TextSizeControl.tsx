import { useState } from 'react';

const sizes = [100, 125, 150, 200];
const storageKey = 'projecthub.text-size';

function savedSize() {
  try {
    const value = Number(localStorage.getItem(storageKey));
    if (sizes.includes(value)) return value;
  } catch { /* Storage is optional. */ }
  return 100;
}

function applySize(size: number) {
  document.documentElement.style.setProperty('--text-scale', String(size / 100));
}

export function applySavedTextSize() {
  applySize(savedSize());
}

export function TextSizeControl() {
  const [size, setSize] = useState(savedSize);
  return (
    <label>
      Text size
      <select aria-label="Text size" value={size} onChange={(event) => {
        const value = Number(event.target.value);
        if (!sizes.includes(value)) return;
        setSize(value);
        applySize(value);
        try { localStorage.setItem(storageKey, String(value)); } catch { /* Storage is optional. */ }
      }}>
        {sizes.map((value) => (
          <option key={value} value={value}>{value}%{value === 100 ? ' (default)' : ''}</option>
        ))}
      </select>
    </label>
  );
}
