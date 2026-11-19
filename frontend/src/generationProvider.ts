export type GenerationProvider = 'codex' | 'claude';
export type Generation = { model: string; effort: string; provider?: GenerationProvider };
export const generationProviderLabels = { codex: 'Codex', claude: 'Claude Code' } as const;

export function generationBody({ model, effort, provider }: Generation) {
  return { model: model || null, effort: effort || null, ...(provider === 'claude' ? { provider } : {}) };
}

export function generationDestination(provider?: GenerationProvider) {
  return provider === 'claude' ? 'Anthropic through Claude Code' : 'OpenAI through Codex';
}
