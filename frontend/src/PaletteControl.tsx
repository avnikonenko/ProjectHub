import { useId, useState } from 'react';

const palettes = [
  { value: 'green', label: 'Green (original)' },
  { value: 'lavender', label: 'Lavender' },
  { value: 'sand', label: 'Warm sand' },
  { value: 'slate', label: 'Slate blue' },
  { value: 'black', label: 'Black' },
];
const presets = [
  { value: 'original', label: 'Original', detail: 'Keep your chosen colour palette.' },
  { value: 'studio', label: 'Studio', detail: 'Clean sans-serif type and a compact layout.' },
  { value: 'editorial', label: 'Editorial', detail: 'Serif text and more space for reading.' },
  { value: 'contrast', label: 'High contrast', detail: 'Brighter text and stronger control borders.' },
];
const appearanceKey = 'projecthub.appearance';

function savedAppearance() {
  try {
    const value = localStorage.getItem(appearanceKey);
    if (presets.some(preset => preset.value === value)) return value!;
  } catch { /* Storage is optional. */ }
  return 'original';
}

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
  document.documentElement.dataset.appearance = savedAppearance();
}

export function PaletteControl() {
  const [palette, setPalette] = useState(savedPalette);
  const [appearance, setAppearance] = useState(savedAppearance);
  const descriptionId = useId();
  const preset = presets.find(option => option.value === appearance) ?? presets[0];
  return (
    <>
      <label>
        App theme
        <select aria-label="App theme" aria-describedby={descriptionId} value={appearance} onChange={(event) => {
          const value = event.target.value;
          if (!presets.some(option => option.value === value)) return;
          setAppearance(value);
          document.documentElement.dataset.appearance = value;
          try { localStorage.setItem(appearanceKey, value); } catch { /* Storage is optional. */ }
        }}>
          {presets.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        <small id={descriptionId}>{preset.detail}</small>
      </label>
      {appearance === 'original' && <label>
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
      </label>}
    </>
  );
}
