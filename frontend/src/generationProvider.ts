export type GenerationProvider = 'codex' | 'claude';
export type Generation = { model: string; effort: string; provider?: GenerationProvider; aiProfile?: string };
export const generationProviderLabels = { codex: 'Codex', claude: 'Claude Code' } as const;

export function generationBody({ model, effort, provider, aiProfile }: Generation, actionId?: string) {
  return {
    model: model || null,
    effort: effort || null,
    ...(actionId ? { generation_action_id: actionId } : {}),
    ...(provider === 'claude' ? { provider, ...(aiProfile ? { ai_profile: aiProfile } : {}) } : {}),
  };
}

export function generationDestination(provider?: GenerationProvider) {
  return provider === 'claude' ? 'Anthropic through Claude Code' : 'OpenAI through Codex';
}
