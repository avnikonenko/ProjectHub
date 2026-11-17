import { useState } from 'react';

const palettes = [
  { value: 'blue', label: 'Blue' },
  { value: 'sage', label: 'Sage' },
  { value: 'lavender', label: 'Lavender' },
  { value: 'amber', label: 'Amber' },
  { value: 'graphite', label: 'Graphite' },
];
const storageKey = 'projecthub.portfolioPalette';

function savedPalette() {
  try {
    const value = localStorage.getItem(storageKey);
    if (palettes.some((palette) => palette.value === value)) return value!;
  } catch { /* Storage is optional. */ }
  return 'blue';
}

export function applySavedPalette() {
  document.documentElement.dataset.palette = savedPalette();
}

export function PaletteControl() {
  const [palette, setPalette] = useState(savedPalette);
  return (
    <label>
      Colour palette
      <select aria-label="Colour palette" value={palette} onChange={(event) => {
        const value = event.target.value;
        if (!palettes.some((option) => option.value === value)) return;
        setPalette(value);
        document.documentElement.dataset.palette = value;
        try { localStorage.setItem(storageKey, value); } catch { /* Storage is optional. */ }
      }}>
        {palettes.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>
  );
}
