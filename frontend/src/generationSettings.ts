import { useEffect, useRef, useState } from 'react';
import { api } from './api';

import type { Generation, GenerationProvider } from './generationProvider';
export type { Generation } from './generationProvider';
export type ProjectCodexAccount = {
  account: string | null;
  options: { id: string; label: string }[];
  include_launch?: boolean;
  launch_label?: string;
};

const defaults: Generation = { model: '', effort: '' };
const storageKey = (base: string) => `projecthub.ai-settings:${base}`;
const providerKey = (base: string) => `${storageKey(base)}:provider`;
const claudeKey = (base: string) => `${storageKey(base)}:claude`;
const claudeProfileKey = (base: string) => `${claudeKey(base)}:profile`;
const accountChanges = new Map<string, Promise<ProjectCodexAccount>>();

export function loadProjectGeneration(base: string, account: string | null): Generation {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey(base)) ?? 'null');
    if (saved?.account === account && typeof saved.model === 'string' && typeof saved.effort === 'string') {
      return { model: saved.model, effort: saved.effort };
    }
  } catch { /* Storage is optional. */ }
  return defaults;
}

export function saveProjectGeneration(base: string, account: string | null, generation: Generation) {
  try {
    localStorage.setItem(storageKey(base), JSON.stringify({ ...generation, account }));
  } catch { /* Storage is optional. */ }
}

export async function readProjectCodexAccount(base: string, signal: AbortSignal) {
  // A workspace may reopen before its previous account change has settled.
  const pending = accountChanges.get(base);
  if (pending) await pending.catch(() => {});
  signal.throwIfAborted();
  return api<ProjectCodexAccount>(`${base}/codex/account`, { signal });
}

export function updateProjectCodexAccount(base: string, account: string | null) {
  if (accountChanges.has(base)) return Promise.reject(new Error('An account change is already in progress.'));
  const pending = api<ProjectCodexAccount>(`${base}/codex/account`, {
    method: 'PATCH', body: JSON.stringify({ account }),
  }).then(result => {
    // The server change outlives the workspace that initiated it.
    try { localStorage.removeItem(storageKey(base)); } catch { /* Storage is optional. */ }
    return result;
  }).finally(() => { accountChanges.delete(base); });
  accountChanges.set(base, pending);
  return pending;
}

export function useProjectGenerationSettings(base: string) {
  const [provider, setProvider] = useState<GenerationProvider>('codex');
  const [claude, setClaude] = useState<Generation>(defaults);
  const [claudeProfile, setClaudeProfile] = useState('');
  const [selection, setSelection] = useState<{ account: ProjectCodexAccount; generation: Generation } | null>(null);
  const [savingAccount, setSavingAccount] = useState(false);
  const [accountError, setAccountError] = useState('');
  const reader = useRef<AbortController | null>(null);
  const workspace = useRef<AbortController | null>(null);

  async function load(owner: AbortController, clearError = true) {
    reader.current?.abort();
    const current = new AbortController();
    reader.current = current;
    try {
      const account = await readProjectCodexAccount(base, current.signal);
      if (current.signal.aborted || owner.signal.aborted) return;
      setSelection(previous => ({
        account,
        generation: previous?.account.account === account.account
          ? previous.generation : loadProjectGeneration(base, account.account),
      }));
      if (clearError) setAccountError('');
    } catch (error) {
      if (!current.signal.aborted && !owner.signal.aborted && clearError) setAccountError((error as Error).message);
    } finally {
      if (reader.current === current) reader.current = null;
    }
  }

  useEffect(() => {
    const owner = new AbortController();
    workspace.current = owner;
    try {
      setProvider(localStorage.getItem(providerKey(base)) === 'claude' ? 'claude' : 'codex');
      setClaudeProfile(localStorage.getItem(claudeProfileKey(base)) ?? '');
      const saved = JSON.parse(localStorage.getItem(claudeKey(base)) ?? 'null');
      if (typeof saved?.model === 'string' && typeof saved.effort === 'string')
        setClaude({ model: saved.model, effort: saved.effort });
      else setClaude(defaults);
    } catch { /* Storage is optional. */ }
    const reload = () => { if (!accountChanges.has(base)) void load(owner); };
    void load(owner);
    window.addEventListener('projecthub:profiles-changed', reload);
    return () => {
      owner.abort(); reader.current?.abort();
      window.removeEventListener('projecthub:profiles-changed', reload);
    };
  }, [base]);

  async function changeAccount(account: string) {
    const owner = workspace.current;
    if (!owner || owner.signal.aborted || accountChanges.has(base)) return;
    reader.current?.abort();
    setSavingAccount(true);
    setAccountError('');
    try {
      const result = await updateProjectCodexAccount(base, account || null);
      if (!owner.signal.aborted) setSelection({ account: result, generation: defaults });
    } catch (error) {
      if (!owner.signal.aborted) {
        setSelection(null);
        setAccountError((error as Error).message);
        await load(owner, false);
      }
    } finally {
      if (!owner.signal.aborted) setSavingAccount(false);
    }
  }

  function changeGeneration(generation: Generation) {
    const selected = { model: generation.model, effort: generation.effort };
    if (provider === 'claude') {
      try { localStorage.setItem(claudeKey(base), JSON.stringify(selected)); } catch { /* Storage is optional. */ }
      setClaude(selected);
      return;
    }
    if (!selection || savingAccount || accountChanges.has(base)) return;
    saveProjectGeneration(base, selection.account.account, selected);
    setSelection({ ...selection, generation: selected });
  }

  function changeProvider(selected: GenerationProvider) {
    try { localStorage.setItem(providerKey(base), selected); } catch { /* Storage is optional. */ }
    setProvider(selected);
  }

  function changeClaudeProfile(selected: string) {
    try {
      if (selected) localStorage.setItem(claudeProfileKey(base), selected);
      else localStorage.removeItem(claudeProfileKey(base));
    } catch { /* Storage is optional. */ }
    setClaudeProfile(selected);
  }

  const codexGeneration = savingAccount ? defaults : selection?.generation ?? defaults;
  return {
    generation: provider === 'claude'
      ? { ...claude, provider, ...(claudeProfile ? { aiProfile: claudeProfile } : {}) }
      : codexGeneration,
    provider, changeProvider, codexGeneration, claudeProfile, changeClaudeProfile,
    accounts: selection?.account ?? null,
    savingAccount, accountError, changeAccount, changeGeneration,
    reloadAccount: () => {
      const owner = workspace.current;
      if (owner && !owner.signal.aborted && !accountChanges.has(base) && !reader.current) void load(owner);
    },
  };
}
