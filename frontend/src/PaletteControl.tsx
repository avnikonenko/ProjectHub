import { useState } from 'react';

const palettes = [
  { value: 'green', label: 'Green (original)' },
  { value: 'lavender', label: 'Lavender' },
  { value: 'sand', label: 'Warm sand' },
  { value: 'slate', label: 'Slate blue' },
];
const storageKey = 'projecthub.palette';

function savedPalette() {
  try {
    const value = localStorage.getItem(storageKey);
    if (palettes.some((palette) => palette.value === value)) return value!;
  } catch { /* Storage is optional. */ }
  return 'green';
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
