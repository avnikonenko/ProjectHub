import { useState } from 'react';
import { SettingsHelp } from './SettingsHelp';

const palettes = [
  { value: 'green', label: 'Green (original)' },
  { value: 'lavender', label: 'Lavender' },
  { value: 'sand', label: 'Warm sand' },
  { value: 'slate', label: 'Slate blue' },
  { value: 'black', label: 'Black' },
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
    <>
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
    <SettingsHelp title="About colour palettes">
      <p>Changes the app's colours immediately and remembers your choice in this browser.</p>
    </SettingsHelp>
    </>
  );
}
