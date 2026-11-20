import { planProjectGeneration, planRetryProjectGeneration, planSelectedProjectGeneration, resolveKnowledgeSchemeDependency, useGenerationRequest,
  type GenerationRequestProps, type ProjectGenerationKind, type KnowledgeGenerationOutcome,
  type KnowledgeSchemeDependency, type GenerationPlan, type SchemeFocus } from './generationBatch';
import { GenerationResults, initializeGenerationSelection, type GenerationResultsState, type GenerationSelection } from './GenerationResults';
import { startReviewPolling } from './reviewPolling';
import { AIEmphasis } from './AIEmphasis';
import { TagGeneration } from './TagGeneration';
import { ALL_TOPICS, topicChoices, filterTopicItems, type TopicGroup } from './topicGroups';
import { TopicPicker } from './TopicPicker';
import { KnowledgePage } from './KnowledgePage';
import { ProjectParameters } from './ProjectParameters';
import { CoverageDetails } from './CoverageDetails';
import type { GenerationDiagnostics } from './generationDiagnostics';
import { knowledgeHref, readKnowledgeLink, type KnowledgeLink } from './knowledge';
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { version } from '../package.json';
import { api, patch, post } from './api';
import { NoteCard } from './NoteCard';
import { LazyNote, useLoadedNote } from './LazyNote';
import { noteEditPatch } from './noteEditing';
import { createEditorIdentity } from './editorIdentity';
import { completedMessages } from './codex';
import { useProjectGenerationSettings, type Generation } from './generationSettings';
import { generationBody, generationDestination, generationProviderLabels, type GenerationProvider } from './generationProvider';
import { GenerationStatus, useGenerationRecovery, SavedGeneration, type GenerationProvenance, isGenerationCancelled, cancelledGenerationMessage } from './GenerationStatus';
import { RevisionHistory } from './RevisionHistory';
import {
  chatGPTTranscriptHref, historyTurnHref, readHistoryLink, transcriptMatch, type HistoryLink, type HistoryTurnLink,
} from './historyLinks';
import { HistorySearch } from './HistorySearch';
import type { Overview, Project, RecordData } from './types';
import { ProjectBrief } from './ProjectBrief';
import { Scheme } from './Scheme';
import { GitHubIssues, githubIssuesUrl, RepositoryOverview, useRepositoryOverview } from './RepositoryOverview';
import { BriefSources, briefReferences, citedSources, type BriefSource } from './BriefReferences';
import { AIResultHeader } from './AIResultHeader';
import { Feedback } from './Feedback';
import { ProjectSearchResults } from './ProjectSearchResults';
import { SaveablePassage, type PassageNoteContext } from './SaveablePassage';
import { ThemeToggle } from './ThemeToggle';
import { groupSharedHistory } from './sharedHistory';
import { TextSizeControl } from './TextSizeControl';
import { ExtractionWorkersControl } from './ExtractionWorkersControl';
import { PaletteControl } from './PaletteControl';
import { SettingsHelp } from './SettingsHelp';
import { DeferredDetails } from './DeferredDetails';
import { AIProfileConnections } from './AIProfileConnections';
import { RestartControl } from './RestartControl';
import { Badge, DeleteButton, EditButton, Empty, ErrorBox, Form, HistoryMarkdown, Markdown, PagedItems,
  descriptionField, label, titleField, type Field } from './ui';

const isChatGPTShare = (note: RecordData) => note.kind === 'document'
  && typeof note.reference === 'string'
  && /^https:\/\/chatgpt\.com\/share\/[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(note.reference);

function sessionCaption(session: RecordData) {
  return [session.title || 'Untitled session', `#${session.id}`, session.topic].filter(Boolean).join(' · ');
}

function SessionIdentifier({ threadId }: { threadId: string }) {
  const [status, setStatus] = useState('');
  return (
    <details className="session-id">
      <summary>Thread <code>{threadId.length > 16 ? `${threadId.slice(0, 8)}…${threadId.slice(-6)}` : threadId}</code></summary>
      <div className="session-id-value">
        <code>{threadId}</code>
        <button type="button" className="secondary" aria-label="Copy full thread ID"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(threadId);
              setStatus('Copied');
            } catch {
              setStatus('Copy unavailable. Select the full ID above to copy it.');
            }
          }}>Copy ID</button>
      </div>
      <Feedback tone={status === 'Copied' ? 'success' : 'info'}>{status}</Feedback>
    </details>
  );
}

function sessionActivity(session: RecordData) {
  return session.codex_activity_at ?? session.last_activity_at ?? session.created_at;
}

const projectFields: Field[] = [
  { name: 'name', label: 'Project name', required: true },
  descriptionField,
  { name: 'goals', label: 'Goals', type: 'textarea' },
  { name: 'status', label: 'Development stage' },
  {
    name: 'repository_path',
    label: 'Existing local Git repository (absolute path, optional)',
  },
  { name: 'git_remote', label: 'Git remote (optional)' },
  { name: 'tags', label: 'Tags (comma separated)' },
];
const projectBody = (data: Record<string, any>) => ({
  ...data,
  repository_path: data.repository_path || null,
  git_remote: data.git_remote || '',
  tags: String(data.tags)
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean),
});

export default function App() {
  const [projects, setProjects] = useState<Pick<Project, 'id' | 'name' | 'pinned'>[]>([]);
  const [selected, setSelected] = useState<number | null>(() => {
    try {
      const value = Number(localStorage.getItem('projecthub.selected-project'));
      return Number.isSafeInteger(value) && value > 0 ? value : null;
    } catch { return null; }
  });
  const [error, setError] = useState('');
  const [adding, setAdding] = useState(false);
  const [pinningProject, setPinningProject] = useState<number | null>(null);
  const [removingProject, setRemovingProject] = useState<number | null>(null);
  const removedProjects = useRef(new Set<number>());
  const [loading, setLoading] = useState(true);
  const [historyLink, setHistoryLink] = useState(() => readHistoryLink(window.location.hash));
  const [knowledgeLink, setKnowledgeLink] = useState(() => readKnowledgeLink(window.location.hash));
  const fetchProjects = useCallback(async () => {
    const response = await api<Pick<Project, 'id' | 'name' | 'pinned'>[]>('/projects?compact=true');
    const data = response.filter((project) => !removedProjects.current.has(project.id));
    setProjects(data);
    setSelected((current) =>
      current !== null && data.some((p) => p.id === current)
        ? current
        : null,
    );
  }, []);
  useEffect(() => {
    fetchProjects()
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [fetchProjects]);
  useEffect(() => {
    const update = () => {
      setHistoryLink(readHistoryLink(window.location.hash));
      setKnowledgeLink(readKnowledgeLink(window.location.hash));
    };
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);
  const linkedProject = historyLink?.projectId ?? knowledgeLink?.projectId;
  const linkedProjectExists = linkedProject !== undefined && projects.some((p) => p.id === linkedProject);
  const selectedProject = linkedProject !== undefined ? (linkedProjectExists ? linkedProject : null) : selected;
  async function toggleProjectPin(project: Pick<Project, 'id' | 'pinned'>) {
    if (pinningProject !== null) return;
    setPinningProject(project.id);
    setError('');
    try {
      await patch(`/projects/${project.id}`, { pinned: !project.pinned });
      await fetchProjects();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setPinningProject(null);
    }
  }
  async function removeProject(project: Pick<Project, 'id' | 'name'>) {
    if (removingProject !== null || !window.confirm(
      `Remove “${project.name}”? This permanently removes the project and its saved data from ProjectHub, including notes, tasks, and generated results.`,
    )) return;
    setRemovingProject(project.id);
    setError('');
    try {
      await api(`/projects/${project.id}`, { method: 'DELETE' });
      removedProjects.current.add(project.id);
      setProjects((current) => current.filter((entry) => entry.id !== project.id));
      setSelected((current) => current === project.id ? null : current);
      const hash = window.location.hash;
      if ((readHistoryLink(hash)?.projectId ?? readKnowledgeLink(hash)?.projectId) === project.id) {
        window.history.replaceState(null, '', window.location.pathname + window.location.search);
        setHistoryLink(null);
        setKnowledgeLink(null);
      }
      try {
        if (localStorage.getItem('projecthub.selected-project') === String(project.id)) {
          localStorage.removeItem('projecthub.selected-project');
        }
      } catch { /* Storage is optional. */ }
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setRemovingProject(null);
    }
  }
  function selectProject(projectId: number) {
    setSelected(projectId);
    try { localStorage.setItem('projecthub.selected-project', String(projectId)); } catch { /* Storage is optional. */ }
    if (historyLink || knowledgeLink) {
      window.history.pushState(null, '', window.location.pathname + window.location.search);
      setHistoryLink(null);
      setKnowledgeLink(null);
    }
  }
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-icon" src={new URL('./favicon.svg', import.meta.url).href} alt="" />
          <div>
            ProjectHub<small>Project workspace</small>
          </div>
        </div>
        <div className="sidebar-label">YOUR PROJECTS</div>
        <div className="project-list">
          {projects.map((p) => (
            <div className="project-row" key={p.id}>
              <button
                className={`project-option ${p.id === selectedProject ? 'selected' : ''}`}
                onClick={() => selectProject(p.id)}
              >
                <span className="project-dot" />
                <span className="project-name">{p.name}</span>
              </button>
              <button type="button" className="icon-edit project-pin"
                aria-label={`${p.pinned ? 'Unpin' : 'Pin'} project: ${p.name}`}
                title={p.pinned ? 'Unpin project' : 'Pin project'}
                aria-pressed={Boolean(p.pinned)} disabled={pinningProject !== null}
                onClick={() => void toggleProjectPin(p)}>
                <svg viewBox="0 0 24 24" width="16" height="16"
                  fill={p.pinned ? 'currentColor' : 'none'} stroke="currentColor"
                  strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="m12 3 2.8 5.7 6.3.9-4.5 4.4 1 6.2-5.6-3-5.6 3 1-6.2L3 9.6l6.2-.9L12 3Z" />
                </svg>
              </button>
            </div>
          ))}
        </div>
        <button className="add-project" onClick={() => setAdding(true)}>
          ＋ Add project
        </button>
        <div className="sidebar-footer">
          <span className="local-dot" /> Local workspace
          <br />
          <small>
            Project data stays on this computer.
            <br />
            AI requests go to the selected provider.
          </small>
          <small className="app-copyright">(c) 2026 Aleksandra Ivanova</small>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="topbar-actions">
            <small className="app-version muted">ProjectHub v{version}</small>
            <ThemeToggle />
            <AppSettings />
          </div>
        </header>
        <ErrorBox error={error} />
        {!loading && (historyLink || knowledgeLink) && !linkedProjectExists && (
          <ErrorBox error="The project in this source link is not available." />
        )}
        {loading ? (
          <Empty>Loading projects…</Empty>
        ) : selectedProject === null ? (
          <main>
            <div className="page-heading">
              <h1>{projects.length ? 'Choose a project' : 'A clear place for project work.'}</h1>
              {!projects.length && <p>Keep conversations, decisions, notes, and next steps together.</p>}
            </div>
            {!projects.length && <button onClick={() => setAdding(true)}>
              Register your first project
            </button>}
          </main>
        ) : (
          <ProjectWorkspace
            key={selectedProject}
            projectId={selectedProject}
            historyLink={linkedProjectExists ? historyLink : null}
            knowledgeLink={linkedProjectExists ? knowledgeLink : null}
            onLeaveHistory={() => selectProject(selectedProject)}
            onProjectChanged={fetchProjects}
            onRemoveProject={removeProject}
            removingProject={removingProject}
          />
        )}
      </div>
      {adding && (
        <Form
          title="Add project"
          fields={projectFields}
          initial={{ status: 'planning' }}
          onClose={() => setAdding(false)}
          onSave={async (data) => {
            const p = await post('/projects', projectBody(data));
            await fetchProjects();
            selectProject(p.id);
          }}
        />
      )}
    </div>
  );
}

function ProjectWorkspace({
  projectId,
  onProjectChanged,
  onRemoveProject,
  removingProject,
  historyLink,
  knowledgeLink,
  onLeaveHistory,
}: {
  projectId: number;
  onProjectChanged: () => Promise<void>;
  onRemoveProject: (project: Pick<Project, 'id' | 'name'>) => Promise<void>;
  removingProject: number | null;
  historyLink: HistoryLink | null;
  knowledgeLink: KnowledgeLink | null;
  onLeaveHistory: () => void;
}) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [selectedView, setSelectedView] = useState('Dashboard');
  const editorIdentity = useRef(createEditorIdentity());
  const activeFormIdentity = useRef<number | null>(null);
  const noteEditIntent = useRef<number | null>(null);
  const [pendingNoteEdit, setPendingNoteEdit] = useState<{ id: number; identity: number } | null>(null);
  function cancelPendingNoteEdit(identity = noteEditIntent.current) {
    if (identity === null || noteEditIntent.current !== identity) return;
    noteEditIntent.current = null;
    if (editorIdentity.current.current(identity)) editorIdentity.current.invalidate();
    if (alive.current) setPendingNoteEdit(null);
  }
  function setView(next: string) {
    cancelPendingNoteEdit();
    setSelectedView(next);
  }
  const view = historyLink ? 'History' : knowledgeLink ? 'Knowledge' : selectedView;
  const [visited, setVisited] = useState({ brief: false, qa: false, scheme: false, knowledge: false });
  const [visitedReviews, setVisitedReviews] = useState(false);
  const [running, setRunning] = useState({ brief: false, qa: false, scheme: false });
  const [analysisState, setAnalysisState] = useState({ busy: false, running: false });
  const [knowledgeSubview, setKnowledgeSubview] = useState<'knowledge' | 'parameters'>('knowledge');
  const [parametersVisited, setParametersVisited] = useState(false);
  const [parameterState, setParameterState] = useState({ busy: false, running: false });
  const [bulkRequests, setBulkRequests] = useState<Record<ProjectGenerationKind, number>>({ brief: 0, knowledge: 0, scheme: 0, qa: 0, parameters: 0 });
  const [bulkPending, setBulkPending] = useState<ProjectGenerationKind[]>([]);
  const [bulkSkipped, setBulkSkipped] = useState<ProjectGenerationKind[]>([]);
  const [bulkRequested, setBulkRequested] = useState(false);
  const [bulkAttemptCount, setBulkAttemptCount] = useState(0);
  const [generationResultsVersion, setGenerationResultsVersion] = useState(0);
  const [generationResultsState, setGenerationResultsState] = useState<GenerationResultsState>({ base: '', data: null, loading: true, error: '' });
  const [generationSelection, setGenerationSelection] = useState<GenerationSelection | null>(null);
  const bulkDispatching = useRef(false);
  const pendingScheme = useRef<KnowledgeSchemeDependency | null>(null);
  const [bulkScheme, setBulkScheme] = useState<'none' | 'waiting' | 'requested' | 'skipped'>('none');
  const [schemeFocus, setSchemeFocus] = useState<SchemeFocus>('balanced');
  const [queuedSchemeFocus, setQueuedSchemeFocus] = useState<SchemeFocus>('balanced');
  useEffect(() => {
    if (knowledgeLink) setKnowledgeSubview('knowledge');
  }, [knowledgeLink?.projectId, knowledgeLink?.publicationId, knowledgeLink?.itemId]);
  useEffect(() => {
    if (view === 'Dashboard') setVisited((previous) => previous.brief ? previous : { ...previous, brief: true });
    if (view === 'AI Q&A') setVisited((previous) => previous.qa ? previous : { ...previous, qa: true });
    if (view === 'Knowledge') setVisited((previous) => previous.knowledge ? previous : { ...previous, knowledge: true });
    if (view === 'Scheme') setVisited((previous) => previous.scheme ? previous : { ...previous, scheme: true });
    if (view === 'Reviews') setVisitedReviews(true);
  }, [view]);
  const base = `/projects/${projectId}`;
  const generationSettings = useProjectGenerationSettings(base);
  const { generation } = generationSettings;
  const [error, setError] = useState('');
  const [form, setForm] = useState<{
    identity: number;
    title: string;
    fields: Field[];
    initial?: any;
    save: (data: any) => Promise<unknown>;
  } | null>(null);
  const [report, setReport] = useState<any>(null);
  const [todoTitle, setTodoTitle] = useState('');
  const [bulkTodos, setBulkTodos] = useState(false);
  const [search, setSearch] = useState('');
  const [results, setResults] = useState<string | null>(null);
  const [searchTarget, setSearchTarget] = useState<{ kind: string; id: number; question?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  const onGenerationResultsState = useCallback((state: GenerationResultsState) => {
    if (state.base !== base) return;
    setGenerationResultsState(state);
    if (state.data && !state.loading && !state.error)
      setGenerationSelection(previous => initializeGenerationSelection(previous, state.data!));
  }, [base]);
  const onGenerationSelection = useCallback((selected: ProjectGenerationKind[]) => {
    setGenerationSelection({ project_id: projectId, selected });
  }, [projectId]);
  const recovery = useGenerationRecovery(base, { includeBrief: true, includeKnowledge: true, includeParameters: true });
  const recheckGeneration = useRef(recovery.recheck); recheckGeneration.current = recovery.recheck;
  const observedGeneration = useRef<Record<ProjectGenerationKind, boolean>>({ brief: false, knowledge: false, scheme: false, qa: false, parameters: false });
  const observeGeneration = useCallback((kind: ProjectGenerationKind, active: boolean) => {
    if (observedGeneration.current[kind] !== active) setGenerationResultsVersion(value => value + 1);
    if (active) recovery.clearError(kind);
    else if (observedGeneration.current[kind]) recheckGeneration.current(kind);
    observedGeneration.current[kind] = active;
  }, [recovery.clearError]);
  const onAnalysisState = useCallback((state: { busy: boolean; running: boolean }) => {
    setAnalysisState(state); observeGeneration('knowledge', state.running);
  }, [observeGeneration]);
  const onParameterState = useCallback((state: { busy: boolean; running: boolean }) => {
    setParameterState(state); observeGeneration('parameters', state.running);
  }, [observeGeneration]);
  const currentGenerationResults = generationResultsState.base === base ? generationResultsState : null;
  const generationRecoveryBusy = {
    brief: recovery.checkingByKind.brief || recovery.running.brief,
    qa: recovery.checkingByKind.qa || recovery.running.qa,
    scheme: recovery.checkingByKind.scheme || recovery.running.scheme,
    knowledge: recovery.checkingByKind.knowledge || recovery.running.knowledge,
    parameters: recovery.checkingByKind.parameters || recovery.running.parameters,
  };
  const generationBusy = {
      brief: running.brief || generationRecoveryBusy.brief, qa: running.qa || generationRecoveryBusy.qa,
      scheme: running.scheme || generationRecoveryBusy.scheme,
      knowledge: analysisState.running || generationRecoveryBusy.knowledge,
      parameters: parameterState.running || generationRecoveryBusy.parameters,
  };
  if (currentGenerationResults?.data && !currentGenerationResults.error) {
    for (const view of currentGenerationResults.data.views)
      if (view.state === 'running' || view.state === 'waiting') generationBusy[view.kind] = true;
  }
  const retryPlan = currentGenerationResults?.data && !currentGenerationResults.error
    ? planRetryProjectGeneration(currentGenerationResults.data.views, generationBusy) : null;
  const selectedGenerations = generationSelection?.project_id === projectId ? generationSelection.selected : [];
  const selectedPlan = currentGenerationResults?.data && !currentGenerationResults.error
    ? planSelectedProjectGeneration(selectedGenerations, currentGenerationResults.data.views, generationBusy) : null;
  const allPlan = planProjectGeneration(generationBusy);
  function dispatchGenerationPlan(plan: GenerationPlan) {
    setBulkRequested(true); setBulkSkipped(plan.schemeSkippedForKnowledge ? ['scheme'] : []);
    setBulkAttemptCount(plan.start.length + Number(plan.waitForKnowledge));
    if (plan.waitForKnowledge || plan.start.includes('scheme')) setQueuedSchemeFocus(schemeFocus);
    pendingScheme.current = plan.waitForKnowledge ? { project: base, request: bulkRequests.knowledge + 1 } : null;
    setBulkScheme(plan.waitForKnowledge ? 'waiting' : plan.schemeSkippedForKnowledge ? 'skipped' : 'none');
    if (!plan.start.length) return;
    bulkDispatching.current = true; setBulkPending(plan.start);
    setGenerationResultsVersion(value => value + 1);
    setVisited({ brief: true, qa: true, scheme: true, knowledge: true }); setParametersVisited(true);
    setBulkRequests(previous => {
      const next = { ...previous }; for (const kind of plan.start) next[kind]++; return next;
    });
  }
  function regenerateAll() {
    if (bulkDispatching.current || !allPlan.start.length) return;
    dispatchGenerationPlan(allPlan);
  }
  function retryUnfinished() {
    if (bulkDispatching.current || !currentGenerationResults
        || currentGenerationResults.loading || currentGenerationResults.error || !retryPlan?.start.length) return;
    dispatchGenerationPlan(retryPlan);
  }
  function regenerateSelected() {
    if (bulkDispatching.current || !currentGenerationResults
        || currentGenerationResults.loading || currentGenerationResults.error || !selectedPlan?.start.length) return;
    dispatchGenerationPlan(selectedPlan);
  }
  function bulkHandled(kind: ProjectGenerationKind, started: boolean) {
    if (!started) setBulkSkipped(previous => previous.includes(kind) ? previous : [...previous, kind]);
    if (kind === 'knowledge' && !started && pendingScheme.current) {
      pendingScheme.current = null;
      setBulkScheme('skipped');
    }
    setBulkPending(previous => {
      const next = previous.filter(value => value !== kind);
      if (!next.length && !pendingScheme.current) bulkDispatching.current = false;
      return next;
    });
  }
  function onKnowledgeOutcome(outcome: KnowledgeGenerationOutcome) {
    if (!alive.current) return;
    const action = resolveKnowledgeSchemeDependency(pendingScheme.current, base, outcome);
    if (action === 'ignore') return;
    pendingScheme.current = null;
    if (action === 'skip') {
      setBulkScheme('skipped');
      if (!bulkPending.length) bulkDispatching.current = false;
      return;
    }
    setBulkScheme('requested');
    if (!bulkPending.length) bulkDispatching.current = false;
    // The server owns the dependent job, so navigating away cannot discard it.
    recheckGeneration.current('scheme');
  }
  useEffect(() => {
    if (historyLink || knowledgeLink) {
      setResults(null);
      setSearchTarget(null);
      setReport(null);
    }
  }, [historyLink, knowledgeLink]);
  function leaveHistory() {
    onLeaveHistory();
    setView('History');
  }
  const load = useCallback(async (signal?: AbortSignal) => {
    const result = await api<Overview>(`${base}/overview?compact=true&note_content=false`, { signal });
    if (alive.current && !signal?.aborted) setOverview(result);
  }, [base]);
  useEffect(() => {
    const controller = new AbortController();
    alive.current = true;
    load(controller.signal).catch((e) => {
      if (!controller.signal.aborted) setError(e.message);
    });
    return () => {
      alive.current = false;
      editorIdentity.current.invalidate();
      activeFormIdentity.current = null;
      noteEditIntent.current = null;
      controller.abort();
    };
  }, [load]);
  async function run(action: () => Promise<unknown>) {
    cancelPendingNoteEdit();
    setError('');
    setBusy(true);
    try {
      await action();
      await load();
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  function showForm(definition: Omit<NonNullable<typeof form>, 'identity'>, requestedIdentity?: number) {
    if (!alive.current || activeFormIdentity.current !== null) return;
    const identity = requestedIdentity ?? editorIdentity.current.begin();
    if (!editorIdentity.current.current(identity)) return;
    activeFormIdentity.current = identity;
    noteEditIntent.current = null;
    setPendingNoteEdit(null);
    setForm({ ...definition, identity, save: async data => {
      if (!editorIdentity.current.current(identity)) throw new Error('This editor is no longer current. Open the record again.');
      await definition.save(data);
    } });
  }
  function closeForm(identity: number) {
    if (editorIdentity.current.current(identity)) editorIdentity.current.invalidate();
    if (activeFormIdentity.current === identity) activeFormIdentity.current = null;
    setForm(previous => previous?.identity === identity ? null : previous);
  }
  function requestNoteEdit(id: number) {
    if (!alive.current || activeFormIdentity.current !== null) return;
    const identity = editorIdentity.current.begin();
    noteEditIntent.current = identity;
    setPendingNoteEdit({ id, identity });
  }
  function edit(
    title: string,
    fields: Field[],
    endpoint: string,
    initial?: any,
    transform: (d: any) => any = (d) => d,
    requestedIdentity?: number,
  ) {
    showForm({
      title,
      fields,
      initial,
      save: async (data) => {
        await (initial ? patch : post)(`${base}${endpoint}`, transform(data));
        await load();
      },
    }, requestedIdentity);
  }
  useEffect(() => { cancelPendingNoteEdit(); }, [view, historyLink, knowledgeLink, results]);
  const repository = useRepositoryOverview(base, view === 'Dashboard' && !results
    ? overview?.project.repository_path ?? null : null, overview?.project.git_remote ?? '');
  if (!overview)
    return (
      <main>
        <ErrorBox error={error} />
        <Empty>
          {error
            ? 'Project could not be loaded.'
            : 'Loading project…'}
        </Empty>
      </main>
    );
  const { project, tasks, notes, activity } = overview;
  const remoteIssuesUrl = repository.status?.github_issues_url ?? githubIssuesUrl(project.git_remote ?? '');
  const latestActivity = [
    ...activity.map((event) => ({
      key: `event:${event.id}`,
      title: label(event.action ?? event.event_type ?? 'Updated'),
      detail: `${event.entity_type ?? ''}${event.entity_id ? ` #${event.entity_id}` : ''}`,
      time: event.created_at,
    })),
    ...(repository.status?.commits ?? []).map((commit) => ({
      key: `commit:${commit.hash}`,
      title: commit.subject || '(no subject)',
      detail: `Git commit · ${commit.hash.slice(0, 8)}`,
      time: commit.committed_at,
    })),
  ].sort((a, b) => (Date.parse(b.time ?? '') || 0) - (Date.parse(a.time ?? '') || 0));
  const todoOrder = (a: RecordData, b: RecordData) =>
    Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)) || b.id - a.id;
  const matchingTodos = searchTarget?.kind === 'tasks' ? tasks.filter((task) => task.id === searchTarget.id) : tasks;
  const activeTodos = matchingTodos.filter((task) => task.status !== 'completed' && task.status !== 'not_planned').sort(todoOrder);
  const notPlannedTodos = matchingTodos.filter((task) => task.status === 'not_planned').sort(todoOrder);
  const completedTodos = matchingTodos.filter((task) => task.status === 'completed').sort(todoOrder);
  const matchingNotes = (searchTarget?.kind === 'notes' ? notes.filter((note) => note.id === searchTarget.id) : [...notes]).reverse();
  const todoRow = (task: RecordData) => (
    <li className="record-row" key={task.id}>
      <div className="todo-content">
        <label>
          <input
            type="checkbox"
            checked={task.status === 'completed'}
            disabled={busy}
            onChange={(event) => {
              const status = event.target.checked ? 'completed' : 'backlog';
              void run(() => patch(`${base}/tasks/${task.id}`, { status }));
            }}
          />
          <span>{task.title}</span>
        </label>
        {task.description && <details><summary>Notes</summary><Markdown>{task.description}</Markdown></details>}
      </div>
      <div className="record-actions">
        <button type="button" className="icon-edit"
          aria-label={`${task.pinned ? 'Unpin' : 'Pin'} TODO: ${task.title}`}
          title={task.pinned ? 'Unpin TODO' : 'Pin TODO'}
          aria-pressed={Boolean(task.pinned)} disabled={busy}
          onClick={() => { void run(() => patch(`${base}/tasks/${task.id}`, { pinned: !task.pinned })); }}>
          <svg viewBox="0 0 24 24" width="16" height="16"
            fill={task.pinned ? 'currentColor' : 'none'} stroke="currentColor"
            strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="m12 3 2.8 5.7 6.3.9-4.5 4.4 1 6.2-5.6-3-5.6 3 1-6.2L3 9.6l6.2-.9L12 3Z" />
          </svg>
        </button>
        <button type="button" className="icon-edit"
          aria-label={`${task.status === 'not_planned' ? 'Move to open TODOs' : 'Mark not planned'}: ${task.title}`}
          title={task.status === 'not_planned' ? 'Move to open TODOs' : 'Mark not planned'}
          aria-pressed={task.status === 'not_planned'} disabled={busy}
          onClick={() => { void run(() => patch(`${base}/tasks/${task.id}`, {
            status: task.status === 'not_planned' ? 'backlog' : 'not_planned',
          })); }}>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
            strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            {task.status === 'not_planned'
              ? <><path d="M3 10h11a6 6 0 0 1 0 12" /><path d="m7 6-4 4 4 4" /></>
              : <><circle cx="12" cy="12" r="9" /><path d="m6 6 12 12" /></>}
          </svg>
        </button>
        <EditButton label={`Edit TODO: ${task.title}`} disabled={busy}
          onClick={() => edit('Edit TODO', [{ ...titleField, maxLength: 500 },
            { name: 'description', label: 'Notes', type: 'textarea', rows: 8 }], `/tasks/${task.id}`, task,
            (data) => ({ title: String(data.title).trim(), description: data.description }))} />
        <DeleteButton label={`Delete TODO: ${task.title}`} disabled={busy} onClick={() => {
            if (window.confirm(`Delete TODO “${task.title}”?`)) {
              void run(() => api(`${base}/tasks/${task.id}`, { method: 'DELETE' }));
            }
          }} />
      </div>
    </li>
  );
  const tabs = [
    'Generation',
    'Dashboard',
    'Knowledge',
    'Scheme',
    'History',
    'AI Q&A',
    'TODO',
    'Notes',
    'Sources',
    'Reviews',
  ];
  return (
    <>
      <div className="project-header">
        <div>
          <p className="eyebrow">#{String(project.id).padStart(2, '0')}</p>
          <h1>{project.name}</h1>
          {project.description && <p className="muted project-description">{project.description}</p>}
        </div>
        <div className="project-actions">
          <button
            className="secondary"
            onClick={() =>
              showForm({
                title: 'Edit project',
                fields: projectFields,
                initial: { ...project, tags: project.tags.join(', ') },
                save: async (data) => {
                  await patch(base, projectBody(data));
                  await load();
                  await onProjectChanged();
                },
              })
            }
          >
            Edit overview
          </button>
          <button type="button" className="secondary" disabled={busy || removingProject !== null}
            onClick={() => void onRemoveProject(project)}>
            {removingProject === project.id ? 'Removing…' : 'Remove project'}
          </button>
          <GenerationSettings base={base} settings={generationSettings} providerLocked={!!bulkPending.length || bulkScheme === 'waiting'} />
        </div>
      </div>
      <nav className="tabs" aria-label="Project sections">
        {tabs.map((t) => (
          <button
            key={t}
            className={view === t ? 'active' : ''}
            aria-current={view === t ? 'page' : undefined}
            onClick={() => {
              if (historyLink || knowledgeLink) onLeaveHistory();
              if (t === 'Knowledge') window.location.hash = knowledgeHref({ projectId });
              setView(t);
              setSearchTarget(null);
              setReport(null);
              setResults(null);
            }}
          >
            {t === 'Dashboard' ? 'Overview' : t}
          </button>
        ))}
      </nav>
      <main>
        <ErrorBox error={error} />
        <form
          className="search-bar"
          onSubmit={async (e) => {
            e.preventDefault();
            if (search.trim()) { cancelPendingNoteEdit(); setResults(search.trim()); }
          }}
        >
          <span aria-hidden="true">⌕</span>
          <input
            type="search"
            maxLength={2000}
            aria-label="Search project"
            placeholder="Search this project…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              if (!e.target.value.trim()) { setResults(null); setSearchTarget(null); }
            }}
          />
          <button className="secondary" disabled={busy || !search.trim()}>
            Search
          </button>
          {results && (
            <button
              type="button"
              className="quiet"
              onClick={() => { setResults(null); setSearch(''); setSearchTarget(null); }}
            >
              Clear
            </button>
          )}
        </form>
        {!results && searchTarget && <p role="status">Showing the selected search result.{' '}
          <button type="button" className="quiet" onClick={() => setSearchTarget(null)}>Show all</button>
        </p>}
        {(['brief', 'qa', 'scheme'] as const).map((kind) => (running[kind] || recovery.running[kind])
          && (kind !== 'brief' || view !== 'Dashboard' || !!results) && (
          <GenerationStatus key={kind} base={base} kind={kind} label={kind === 'brief' ? 'Project Overview' : kind === 'qa' ? 'AI Q&A' : 'Scheme'} />
        ))}
        {(analysisState.running || recovery.running.knowledge) && (view !== 'Knowledge' || knowledgeSubview !== 'knowledge' || !!results) &&
          <GenerationStatus base={base} kind="knowledge" label="Knowledge Summary"
            sharedProgress={recovery.running.knowledge ? recovery.progress.knowledge : undefined} />}
        {(parameterState.running || recovery.running.parameters) && (view !== 'Knowledge' || knowledgeSubview !== 'parameters' || !!results) &&
          <GenerationStatus base={base} kind="parameters" label="Implementation Choices & Limits"
            sharedProgress={recovery.running.parameters ? recovery.progress.parameters : undefined} />}
        <ErrorBox error={recovery.error} />
        {bulkRequested && <Feedback>{bulkPending.length
          ? `Starting ${bulkPending.length} remaining generation${bulkPending.length === 1 ? '' : 's'}…`
          : bulkAttemptCount ? 'Generation requested. Each view keeps its saved result if generation fails.'
            : 'All views are already busy; their existing runs continue.'}
          {bulkScheme === 'waiting' && <> Scheme is waiting for Knowledge to be saved.</>}
          {bulkScheme === 'skipped' && <> Scheme was skipped because fresh Knowledge was not saved or a required view was busy.</>}
          {!!bulkSkipped.length && <> Some views were skipped because their own generation or controls were busy or not ready.</>}
        </Feedback>}
        <div hidden={view !== 'Generation' || !!results}>
          <GenerationResults base={base} refreshVersion={generationResultsVersion} onState={onGenerationResultsState}
            provider={generation.provider}
            schemeFocus={schemeFocus} onSchemeFocusChange={setSchemeFocus} schemeFocusDisabled={!!bulkPending.length || bulkScheme === 'waiting'}
            selected={selectedGenerations} onSelectionChange={onGenerationSelection} busy={generationBusy}
            onRegenerateSelected={regenerateSelected} regenerateDisabled={bulkPending.length > 0
              || bulkScheme === 'waiting' || !selectedPlan?.start.length}
            bulkActions={<>
              <button className="secondary" disabled={!allPlan.start.length || bulkPending.length > 0 || bulkScheme === 'waiting'} onClick={regenerateAll}>
                {bulkPending.length ? 'Starting generations…' : 'Regenerate all'}
              </button>
              <button className="secondary" disabled={bulkPending.length > 0 || bulkScheme === 'waiting'
                || !currentGenerationResults || currentGenerationResults.loading || !!currentGenerationResults.error
                || !retryPlan?.start.length} onClick={retryUnfinished}>Retry unfinished</button>
            </>} />
        </div>
        {(visited.brief || view === 'Dashboard') && (
          <div hidden={view !== 'Dashboard' || !!results}>
            <ProjectBrief base={base} evidenceVersion={project.updated_at}
              visible={view === 'Dashboard' && !results} generation={generation}
              recoveryVersion={recovery.version.brief} recoveryBusy={generationRecoveryBusy.brief}
              onRecoveryNeeded={() => recovery.recheck('brief')}
              generationRequest={bulkRequests.brief} onGenerationRequestHandled={started => bulkHandled('brief', started)}
              onGenerating={(active) => { observeGeneration('brief', active); setRunning(previous => ({ ...previous, brief: active })); }} />
          </div>
        )}
        {(visited.knowledge || view === 'Knowledge') && (
          <div hidden={view !== 'Knowledge' || !!results}>
            <nav className="tabs" aria-label="Knowledge views">
              <button type="button" className={knowledgeSubview === 'knowledge' ? 'active' : ''}
                aria-current={knowledgeSubview === 'knowledge' ? 'page' : undefined}
                onClick={() => setKnowledgeSubview('knowledge')}>Knowledge Summary & records</button>
              <button type="button" className={knowledgeSubview === 'parameters' ? 'active' : ''}
                aria-current={knowledgeSubview === 'parameters' ? 'page' : undefined}
                onClick={() => { setParametersVisited(true); setKnowledgeSubview('parameters'); }}>Implementation Choices &amp; Limits</button>
            </nav>
            <div hidden={knowledgeSubview !== 'knowledge'}>
            <KnowledgePage base={base} generation={generation} link={knowledgeLink}
              visible={view === 'Knowledge' && knowledgeSubview === 'knowledge' && !results}
              showRecords onBusyChange={onAnalysisState} refreshRequest={bulkRequests.knowledge}
              onGenerationRequestHandled={started => bulkHandled('knowledge', started)}
              onGenerationOutcome={onKnowledgeOutcome}
              generateSchemeAfter={bulkScheme === 'waiting'} schemeFocus={queuedSchemeFocus} />
            </div>
            {parametersVisited && <div hidden={knowledgeSubview !== 'parameters'}>
              <ProjectParameters base={base} generation={generation}
                visible={view === 'Knowledge' && knowledgeSubview === 'parameters' && !results}
                generationRequest={bulkRequests.parameters} onGenerationRequestHandled={started => bulkHandled('parameters', started)}
                onBusyChange={onParameterState} />
            </div>}
          </div>
        )}
        {(visited.qa || view === 'AI Q&A') && (
          <div hidden={view !== 'AI Q&A' || !!results}>
            <ProjectQA base={base} generation={generation} visible={view === 'AI Q&A' && !results}
              generationRequest={bulkRequests.qa} onGenerationRequestHandled={started => bulkHandled('qa', started)}
              targetQuestion={searchTarget?.kind === 'qa' ? searchTarget.question : undefined}
              recoveryVersion={recovery.version.qa} recoveryBusy={generationRecoveryBusy.qa}
              onGenerating={(active) => { observeGeneration('qa', active); setRunning((previous) => ({ ...previous, qa: active })); }} />
          </div>
        )}
        {(visited.scheme || view === 'Scheme') && (
          <div hidden={view !== 'Scheme' || !!results}>
            <Scheme base={base} generation={generation} visible={view === 'Scheme' && !results}
              focus={schemeFocus} queuedFocus={queuedSchemeFocus} onFocusChange={setSchemeFocus}
              waitingForKnowledge={bulkScheme === 'waiting' || (bulkScheme === 'requested' && recovery.checkingByKind.scheme)
                || recovery.progress.scheme?.latest_attempt?.state === 'waiting'}
              generationRequest={bulkRequests.scheme} onGenerationRequestHandled={started => bulkHandled('scheme', started)}
              recoveryVersion={recovery.version.scheme} recoveryBusy={generationRecoveryBusy.scheme}
              onGenerating={(active) => { observeGeneration('scheme', active); setRunning((previous) => ({ ...previous, scheme: active })); }} />
          </div>
        )}
        {(visitedReviews || view === 'Reviews') && (
          <div hidden={view !== 'Reviews' || !!results}>
            <AskProject base={base} projectId={projectId} generation={generation} visible={view === 'Reviews' && !results} />
          </div>
        )}
        {results ? (
          <ProjectSearchResults key={results} base={base} query={results}
            onOpenSource={() => { setResults(null); setSearchTarget(null); }} onOpenRecord={(kind, id, title) => {
            const targetView: Record<string, string> = { tasks: 'TODO', notes: 'Notes', qa: 'AI Q&A', sessions: 'Sources' };
            const index = Number(id.split(':').at(-1));
            if (!targetView[kind] || !Number.isSafeInteger(index) || index < 0) return;
            onLeaveHistory();
            setSearchTarget({ kind, id: index, ...(kind === 'qa' ? { question: title } : {}) });
            setView(targetView[kind]);
            setResults(null);
          }} />
        ) : (
          <>
            {view === 'Dashboard' && (
              <>
                <div className="metrics">
                  <div className="metric">
                    <strong>{notes.length}</strong>
                    <span>Notes</span>
                  </div>
                </div>
                <div className="dashboard-details">
                  {(project.goals.trim() || project.repository_path || remoteIssuesUrl || project.tags.length > 0) && (
                    <section className="card">
                      <h2>Project overview</h2>
                      {project.goals.trim() && (
                        <>
                          <h4>Goals</h4>
                          <Markdown>{project.goals}</Markdown>
                        </>
                      )}
                      {project.repository_path && (
                        <RepositoryOverview path={project.repository_path} {...repository} />
                      )}
                      {remoteIssuesUrl && <GitHubIssues key={remoteIssuesUrl} base={base} url={remoteIssuesUrl} />}
                      {project.tags.length > 0 && (
                        <div className="tags">
                          {project.tags.map((tag) => <span key={tag}>{tag}</span>)}
                        </div>
                      )}
                    </section>
                  )}
                  <section className="card">
                    <h2>Latest activity</h2>
                    <PagedItems label="Activity pages">{latestActivity.map((item) => (
                      <div className="activity" key={item.key}>
                        <span className="activity-dot" />
                        <div>
                          <strong>{item.title}</strong>
                          <p>{item.detail}</p>
                          {item.time && <small>{new Date(item.time).toLocaleString()}</small>}
                        </div>
                      </div>
                    ))}
                    </PagedItems>
                    {!latestActivity.length && <Empty>No activity recorded.</Empty>}
                  </section>
                </div>
              </>
            )}
            {view === 'History' && (
              <ProjectHistory base={base} projectId={projectId} historyLink={historyLink}
                notes={notes} onLeaveHistory={leaveHistory}
                onNoteSaved={() => load().catch((e) => setError(e.message))} />
            )}
            {view === 'TODO' && (
              <section className="card">
                <h2>TODO</h2>
                <form className="todo-add" onSubmit={(event) => {
                  event.preventDefault();
                  const title = todoTitle.trim();
                  if (busy || !title) return;
                  void run(async () => {
                    await post(`${base}/tasks`, { title });
                    setTodoTitle('');
                  });
                }}>
                  <input aria-label="New TODO" placeholder="Add something to do…" maxLength={500}
                    value={todoTitle} onChange={(event) => setTodoTitle(event.target.value)} />
                  <button disabled={busy || !todoTitle.trim()}>Add</button>
                </form>
                <button className="quiet" onClick={() => setBulkTodos(true)}>Add multiple TODOs</button>
                {bulkTodos && (
                  <Form title="Add multiple TODOs"
                    fields={[{ name: 'items', label: 'One TODO per line (up to 100)', type: 'textarea', rows: 8, required: true }]}
                    onClose={() => setBulkTodos(false)}
                    onSave={async ({ items }) => {
                      const titles = String(items).split(/\r?\n/).map((title) => title.trim()).filter(Boolean);
                      if (!titles.length || titles.length > 100) throw new Error('Enter between 1 and 100 TODOs.');
                      if (titles.some((title) => title.length > 500)) throw new Error('Each TODO must be 500 characters or fewer.');
                      await post(`${base}/tasks/bulk`, { items: titles.map((title) => ({ title })) });
                      await load();
                    }} />
                )}
                {activeTodos.length
                  ? <PagedItems label="Open TODO pages" listClass="todo-list" pageSize={10}>{activeTodos.map(todoRow)}</PagedItems>
                  : <Empty>No open TODOs.</Empty>}
                {!!notPlannedTodos.length && (
                  <details open={searchTarget?.kind === 'tasks' ? true : undefined}>
                    <summary>Not planned ({notPlannedTodos.length})</summary>
                    <PagedItems label="Not planned TODO pages" listClass="todo-list" pageSize={10}>{notPlannedTodos.map(todoRow)}</PagedItems>
                  </details>
                )}
                {!!completedTodos.length && (
                  <details open={searchTarget?.kind === 'tasks' ? true : undefined}>
                    <summary>Completed ({completedTodos.length})</summary>
                    <PagedItems label="Completed TODO pages" listClass="todo-list" pageSize={10}>{completedTodos.map(todoRow)}</PagedItems>
                  </details>
                )}
              </section>
            )}
            {view === 'Notes' && (
              <>
                <div className="section-title">
                  <div>
                    <h2>Notes</h2>
                    <p className="muted">
                      Notes, assumptions, references, and imported text.
                      Imported content is untrusted data.
                    </p>
                  </div>
                  <button
                    onClick={() =>
                      edit(
                        'New note',
                        [
                          titleField,
                          {
                            name: 'content',
                            label: 'Markdown content',
                            type: 'textarea',
                          },
                          {
                            name: 'kind',
                            label: 'Kind',
                            type: 'select',
                            options: [
                              'note',
                              'assumption',
                              'evidence',
                              'limitation',
                              'proposal',
                            ],
                          },
                          {
                            name: 'reference',
                            label: 'Document URL or repository file reference',
                          },
                        ],
                        '/notes',
                      )
                    }
                  >
                    ＋ New note
                  </button>
                </div>
                <div className="toolbar">
                  <label className="upload-label">
                    Import text / Markdown{' '}
                    <input
                      aria-label="Import document"
                      type="file"
                      accept=".txt,.md"
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (!file) return;
                        e.target.value = '';
                        if (file.size > 2 * 1024 * 1024) {
                          setError('Maximum upload size is 2 MiB.');
                          return;
                        }
                        run(async () => {
                          const formData = new FormData();
                          formData.append('file', file);
                          await api(`${base}/documents`, {
                            method: 'POST',
                            body: formData,
                          });
                        });
                      }}
                    />
                  </label>
                  <a
                    className="button secondary"
                    href={`/api${base}/export`}
                    download={`${project.name}-project.json`}
                  >
                    Export project JSON
                  </a>
                </div>
                {!matchingNotes.length && <Empty>No notes yet.</Empty>}
                <PagedItems label="Note pages">{matchingNotes.map((n) => (
                  <NoteCard key={n.id} base={base} note={n} shared={isChatGPTShare(n)} busy={busy}
                    reveal={searchTarget?.kind === 'notes'}
                    editRequest={pendingNoteEdit?.id === n.id ? pendingNoteEdit.identity : undefined}
                    onEditIntent={() => requestNoteEdit(n.id)}
                    onEditCancel={cancelPendingNoteEdit}
                    onEditReady={(loaded, identity) => edit('Edit note', [titleField,
                      { name: 'content', label: 'Markdown content', type: 'textarea' },
                      { name: 'kind', label: 'Kind' }, { name: 'reference', label: 'Reference' },
                    ], `/notes/${n.id}`, loaded, values => noteEditPatch(values, loaded), identity)}
                    onDelete={() => {
                      if (window.confirm(`Delete note “${n.title}”?`)) {
                        void run(() => api(`${base}/notes/${n.id}`, { method: 'DELETE' }));
                      }
                    }} reference={
                    n.reference && (
                      <p>
                        <strong>Reference:</strong>{' '}
                        {isChatGPTShare(n)
                          ? <a href={n.reference} target="_blank" rel="noreferrer">Open shared chat</a>
                          : typeof n.reference === 'string' && readHistoryLink(n.reference)?.projectId === projectId
                          ? <a href={n.reference}>Open source conversation</a>
                          : <span>{n.reference}</span>}
                      </p>
                    )
                    } />
                ))}
                </PagedItems>
              </>
            )}
            {view === 'Sources' && (
              <>
                <Codex base={base} onError={setError}
                  targetSessionId={searchTarget?.kind === 'sessions' ? searchTarget.id : undefined}
                  notes={notes} onImported={load}
                  onUnlinked={(sessionId) => {
                    setSearchTarget((current) => current?.kind === 'sessions' && current.id === sessionId ? null : current);
                    setGenerationResultsVersion((version) => version + 1);
                    return load();
                  }}
                  onOpenGeneration={() => setView('Generation')} />
                <ChatGPTShares base={base} notes={notes} showHistory={false} />
              </>
            )}
            {view === 'Reviews' && (
                <details>
                  <summary className="review-discussion-toggle">Continue a Codex discussion</summary>
                  <CodexReview base={base} generation={generationSettings.codexGeneration} onNotesChanged={load}
                    onManageSessions={() => { setReport(null); setView('Sources'); }} />
                </details>
            )}
          </>
        )}
        {report !== null && (
          <section className="card report">
            <div className="section-title">
              <h2>{view === 'Reviews' ? 'Review report' : 'Project report'}</h2>
              <button className="quiet" onClick={() => setReport(null)}>
                Close
              </button>
            </div>
            <Report value={report} />
          </section>
        )}
      </main>
      {form && (
        <Form
          key={`${base}:${form.identity}`}
          title={form.title}
          fields={form.fields}
          initial={form.initial}
          onSave={form.save}
          onClose={() => closeForm(form.identity)}
        />
      )}
    </>
  );
}

function Report({ value }: { value: any }) {
  if (typeof value === 'string') return <Markdown>{value}</Markdown>;
  if (value?.markdown) return <Markdown>{value.markdown}</Markdown>;
  if (value?.content && typeof value.content === 'string')
    return (
      <>
        {value.version && <small>Context version: {value.version}</small>}
        <Markdown>{value.content}</Markdown>
      </>
    );
  if (Array.isArray(value?.findings))
    return (
      <>
        <p className="muted">Deterministic review of stored records.</p>
        <PagedItems label="Review finding pages">{value.findings.map((finding: any, index: number) => (
          <article className="compact-row" key={index}>
            <Badge
              value={finding.category ?? finding.severity ?? 'unverified'}
            />
            {finding.title && <h3>{finding.title}</h3>}
            <p>{finding.message ?? finding.detail}</p>
          </article>
        ))}</PagedItems>
        {!value.findings.length && <Empty>No rule-based findings.</Empty>}
        {value.limitations?.map((text: string) => (
          <p key={text}>{text}</p>
        ))}
      </>
    );
  if (Array.isArray(value))
    return (
      <>
        <PagedItems label="Record pages">{value.map((item: any, index: number) => (
          <article className="compact-row" key={item.id ?? index}>
            <h3>{label(item.action ?? item.title ?? 'Record')}</h3>
            <small>
              {item.entity_type} #{item.entity_id} · {item.created_at}
            </small>
            <details>
              <summary>Recorded history</summary>
              <pre className="json-report">{JSON.stringify(item, null, 2)}</pre>
            </details>
          </article>
        ))}
        {!value.length && <Empty>No records.</Empty>}</PagedItems>
      </>
    );
  return <pre className="json-report">{JSON.stringify(value, null, 2)}</pre>;
}

type SavedQA = {
  diagnostics?: GenerationDiagnostics; coverage?: string[];
  topic_grouping?: Record<string, unknown>;
  generated_at: string;
  generation?: GenerationProvenance | null;
  topic_groups?: TopicGroup[];
  items: { id?: string; question: string; answer: string; source_ids: string[];
    variants?: { answer: string; source_ids: string[] }[] }[];
  sources: BriefSource[];
};

type GenerationProps = { base: string; generation: Generation };
type ProjectAnswer = { answer: string; sources: BriefSource[]; provider?: GenerationProvider };

function AskProject({ base, projectId, generation, visible }: GenerationProps & { projectId: number; visible: boolean }) {
  const [question, setQuestion] = useState('');
  const [result, setResult] = useState<ProjectAnswer | null>(null);
  const [answeredQuestion, setAnsweredQuestion] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [remoteBusy, setRemoteBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function ask() {
    const query = question.trim();
    if (!query || request.current || remoteBusy) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError('');
    try {
      const response = await api<ProjectAnswer>(`${base}/codex/ask`, {
        method: 'POST',
        body: JSON.stringify({ question: query, confirmed: true, ...generationBody(generation) }),
        signal: controller.signal,
      });
      if (!controller.signal.aborted) {
        setResult(response);
        setAnsweredQuestion(query);
      }
    } catch (e) {
      if (!controller.signal.aborted && !isGenerationCancelled(e)) setError((e as Error).message);
    } finally {
      if (request.current === controller) request.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  const prefix = `${projectId}-ask`;
  const numbers = new Map(result?.sources.map((source, index) => [source.id, index + 1]));
  return (
    <section className="card project-brief" aria-busy={busy || remoteBusy}>
      <h2>Ask Project</h2>
      <p className="brief-note">
        ProjectHub searches saved conversations and notes locally. Your question and matching passages
        are sent to {generationDestination(generation.provider)} to generate an answer. The answer is not saved.
      </p>
      <form onSubmit={(event) => { event.preventDefault(); void ask(); }}>
        <label>
          Project question
          <textarea rows={3} required maxLength={2000} value={question} disabled={busy || remoteBusy}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="What have we already tried?" />
        </label>
        <button disabled={busy || remoteBusy || !question.trim()}>{busy || remoteBusy ? 'Asking…' : 'Ask Project'}</button>
      </form>
      {(visible || busy) && <GenerationStatus base={base} kind="ask" label="Project answer" onRunningChange={setRemoteBusy} />}
      <ErrorBox error={error} />
      {result && (
        <section className="brief-section">
          <h3>{busy || remoteBusy ? 'Previous answer' : 'Answer'}</h3>
          {result.provider && generationProviderLabels[result.provider] &&
            <small className="muted">Generated by {generationProviderLabels[result.provider]}</small>}
          {answeredQuestion && <p className="muted">{answeredQuestion}</p>}
          <p className="shared-transcript"><AIEmphasis text={result.answer} renderText={text => text.split(/(\[\d+\])/g).map((part, index) => {
            const id = /^\[(\d+)\]$/.exec(part)?.[1];
            return id && numbers.has(id)
              ? <span key={index}>{briefReferences([id], numbers, prefix)}</span>
              : part;
          })} /></p>
          {!!result.sources.length && (
            <details className="brief-sources">
              <summary>Sources ({result.sources.length})</summary>
              <BriefSources sources={result.sources} prefix={prefix} openInNewTab />
            </details>
          )}
        </section>
      )}
    </section>
  );
}

function useSettingsDismiss(dialogOpen = false) {
  const panel = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    if (dialogOpen) return;
    function dismiss(event: PointerEvent) {
      if (event.target instanceof Node && !panel.current?.contains(event.target)) {
        panel.current?.removeAttribute('open');
      }
    }
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, [dialogOpen]);
  return panel;
}

function AppSettings() {
  const [addingProfile, setAddingProfile] = useState(false);
  const panel = useSettingsDismiss(addingProfile);
  const [open, setOpen] = useState(false);
  const [aiSettingsVersion, setAISettingsVersion] = useState(0);
  const [profileMessage, setProfileMessage] = useState('');
  return (
    <>
      <details ref={panel} className="generation-settings" onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary className="button secondary" aria-label="App settings" title="App settings">⚙</summary>
        <div className="generation-options">
          <h3>Settings</h3>
          <PaletteControl />
          <TextSizeControl />
          <SettingsHelp title="About appearance">
            <p>Colour palette, light or dark mode, and text size can be adjusted independently. Your choices are saved in this browser.</p>
          </SettingsHelp>
          <ExtractionWorkersControl active={open} refreshVersion={aiSettingsVersion} />
          <DeferredDetails className="settings-help" summary={<summary>AI profiles</summary>}>
            {() => <>
              {open && <AIProfileConnections refreshVersion={aiSettingsVersion} />}
              <button type="button" className="secondary"
                onClick={() => { setProfileMessage(''); setAddingProfile(true); }}>
                Add AI profile
              </button>
              <AIProfileHelp />
            </>}
          </DeferredDetails>
          <Feedback tone="success">{profileMessage}</Feedback>
          <RestartControl active={open} onRestarted={() => setAISettingsVersion(version => version + 1)} />
        </div>
      </details>
      {addingProfile && <Form
        title="Add AI profile"
        fields={[
          { name: 'provider', label: 'AI provider', type: 'select', options: Object.values(generationProviderLabels) },
          { name: 'name', label: 'Profile name', required: true, maxLength: 80 },
          { name: 'directory', label: 'Signed-in profile directory (absolute path)', required: true, maxLength: 4096 },
        ]}
        saveLabel="Add profile" savingLabel="Checking profile…"
        onClose={() => setAddingProfile(false)}
        onSave={async ({ provider, name, directory }) => {
          const providerId = provider === generationProviderLabels.claude ? 'claude' : 'codex';
          const result = await post('/settings/ai-profiles', { provider: providerId, name, directory });
          window.dispatchEvent(new Event('projecthub:profiles-changed'));
          setAISettingsVersion(version => version + 1);
          const profileName = typeof result.label === 'string' ? result.label : 'Profile';
          setProfileMessage(result.is_launch
            ? `${profileName} is the ProjectHub launch account. Choose it in project AI settings.`
            : result.added === false
              ? `${profileName} is already available in project AI settings.`
              : `${profileName} added. Choose it in project AI settings.`);
        }}
      />}
    </>
  );
}

function AIProfileHelp() {
  return <SettingsHelp title="About AI profiles">
    <p>Add a signed-in Codex or Claude Code directory, then select its profile in the project’s AI settings.</p>
  </SettingsHelp>;
}

function ProjectAIProfileControl({ active, settings, locked }: {
  active: boolean;
  settings: ReturnType<typeof useProjectGenerationSettings>;
  locked: boolean;
}) {
  const { provider, accounts, accountError, savingAccount, changeAccount, claudeProfile, changeClaudeProfile } = settings;
  const isClaude = provider === 'claude';
  type Profiles = { options: { id: string; label: string }[]; launch_label: string };
  const [profiles, setProfiles] = useState<Profiles | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!active || !isClaude) return;
    let request: AbortController;
    function load() {
      request?.abort();
      const current = new AbortController();
      request = current;
      setError('');
      api<Profiles>('/settings/ai-profiles?provider=claude', { signal: current.signal })
        .then(result => { if (!current.signal.aborted) setProfiles(result); })
        .catch((e: Error) => { if (!current.signal.aborted) setError(e.message); });
    }
    load();
    window.addEventListener('projecthub:profiles-changed', load);
    return () => {
      request.abort();
      window.removeEventListener('projecthub:profiles-changed', load);
    };
  }, [active, isClaude]);
  const selected = isClaude ? claudeProfile : accounts?.account ?? '';
  const options = isClaude ? profiles?.options : accounts?.options;
  const launchLabel = isClaude ? profiles?.launch_label ?? 'Claude Code launch account'
    : accounts?.launch_label || 'ProjectHub launch account';
  return <>
    <label>
      AI profile for this project
      <select value={selected} disabled={locked || (isClaude ? !profiles && !selected : !accounts || savingAccount)}
        onChange={event => (isClaude ? changeClaudeProfile : changeAccount)(event.target.value)}>
        {(isClaude || accounts?.include_launch !== false) && <option value="">{launchLabel}</option>}
        {isClaude && selected && !options?.some(option => option.id === selected) &&
          <option value={selected}>{profiles ? 'Unavailable profile' : 'Selected profile'}</option>}
        {options?.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
      </select>
    </label>
    <ErrorBox error={isClaude ? error : accountError} />
  </>;
}

function GenerationSettings({ base, settings, providerLocked }: {
  base: string; settings: ReturnType<typeof useProjectGenerationSettings>;
  providerLocked: boolean;
}) {
  const panel = useSettingsDismiss();
  const [open, setOpen] = useState(false);
  const [visited, setVisited] = useState(false);
  const [instructions, setInstructions] = useState<string | null>(null);
  const [loadingInstructions, setLoadingInstructions] = useState(false);
  const [instructionsError, setInstructionsError] = useState('');
  const [instructionsSaved, setInstructionsSaved] = useState(false);
  const instructionsPath = `${base}/codex/instructions`;

  async function openInstructions() {
    setInstructionsError('');
    setInstructionsSaved(false);
    setLoadingInstructions(true);
    try {
      const result = await api<{ instructions: string }>(instructionsPath);
      setInstructions(result.instructions);
    } catch (e) {
      setInstructionsError((e as Error).message);
    } finally {
      setLoadingInstructions(false);
    }
  }

  return (
    <>
      <details ref={panel} className="generation-settings" onToggle={(event) => {
        const isOpen = event.currentTarget.open;
        setOpen(isOpen);
        if (isOpen) {
          setVisited(true);
          settings.reloadAccount();
        }
      }}>
        <summary className="button secondary" aria-label="AI settings" title="AI settings">⚙</summary>
        {visited && <GenerationOptions active={open} base={base} settings={settings} providerLocked={providerLocked}>
          <button type="button" className="secondary" disabled={loadingInstructions} onClick={openInstructions}>
            {loadingInstructions ? 'Loading instructions…' : 'Project AI instructions'}
          </button>
          <SettingsHelp title="About project AI instructions">
            <p>Set response preferences for future AI requests in this project. You can edit the shared recommendations or leave the instructions blank. Read-only and evidence rules still apply.</p>
          </SettingsHelp>
          <ErrorBox error={instructionsError} />
          {instructionsSaved && <Feedback tone="success">Instructions saved.</Feedback>}
        </GenerationOptions>}
      </details>
      {instructions !== null && <Form
        title="Project AI instructions"
        fields={[{ name: 'instructions', label: 'Edit or remove recommendations for this project. Leave blank for none.',
          type: 'textarea', rows: 10, maxLength: 16002 }]}
        initial={{ instructions }}
        onClose={() => setInstructions(null)}
        onSave={async (values) => {
          if (values.instructions !== instructions) {
            await patch(instructionsPath, { instructions: values.instructions });
            setInstructionsSaved(true);
          }
        }}
      />}
    </>
  );
}

function GenerationOptions({ active, base, settings, providerLocked, children }: {
  active: boolean; base: string;
  settings: ReturnType<typeof useProjectGenerationSettings>;
  providerLocked: boolean;
  children: React.ReactNode;
}) {
  const { accounts, savingAccount, changeGeneration, generation, provider, changeProvider } = settings;
  type Model = { id: string; label: string; efforts: string[] };
  const [modelList, setModelList] = useState<{ key: string; models: Model[] } | null>(null);
  const [status, setStatus] = useState('');
  const account = accounts?.account;
  const providerLabel = generationProviderLabels[provider];
  const canChooseModel = provider === 'claude' || (!!accounts && !savingAccount);
  const modelKey = provider === 'claude' ? 'claude' : `codex:${account ?? ''}`;
  const models = modelList?.key === modelKey ? modelList.models : [];
  useEffect(() => {
    if (!active || !canChooseModel) return;
    const controller = new AbortController();
    setModelList(null);
    setStatus(`Loading ${providerLabel} models…`);
    api<Model[]>(`${base}/codex/models${provider === 'claude' ? '?provider=claude' : ''}`, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return;
        setModelList({ key: modelKey, models: result });
        setStatus(result.length ? '' : `No models reported by ${providerLabel}.`);
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted) setStatus(error.message || `Could not load ${providerLabel} models.`);
      });
    return () => controller.abort();
  }, [active, base, canChooseModel, modelKey, provider, providerLabel]);
  const model = models.find((option) => option.id === generation.model);
  const efforts = model?.efforts ?? [];
  return (
    <div className="generation-options">
      <strong>AI settings</strong>
      <label>
        AI generation provider
        <select value={provider} disabled={providerLocked}
          onChange={(event) => changeProvider(event.target.value as GenerationProvider)}>
          <option value="codex">Codex</option>
          <option value="claude">Claude Code</option>
        </select>
      </label>
      <ProjectAIProfileControl active={active} settings={settings} locked={providerLocked} />
      <AIProfileHelp />
      <label>
        Model
        <select value={generation.model} disabled={!canChooseModel} onChange={(e) => changeGeneration({ model: e.target.value, effort: '' })}>
          <option value="">{providerLabel} default</option>
          {generation.model && !model && <option value={generation.model}>{generation.model}</option>}
          {models.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
        </select>
      </label>
      <SettingsHelp title="About model selection">
        <p>Codex defaults to the selected account's configured model. Claude Code defaults to Sonnet. Choosing another model resets reasoning effort. Your choices are saved for this project and provider.</p>
      </SettingsHelp>
      <label>
        Reasoning effort
        <select value={generation.effort} disabled={!canChooseModel || !efforts.length}
          onChange={(e) => changeGeneration({ ...generation, effort: e.target.value })}>
          <option value="">Model default</option>
          {generation.effort && !efforts.includes(generation.effort) && <option value={generation.effort}>{generation.effort}</option>}
          {efforts.map((effort) => <option key={effort} value={effort}>{label(effort)}</option>)}
        </select>
      </label>
      <Feedback>{status}</Feedback>
      <SettingsHelp title="About reasoning effort">
        <p>Choose a model to see its available reasoning levels. Higher effort can take longer and consume more tokens. An explicit reasoning choice takes precedence over a knowledge profile's default.</p>
      </SettingsHelp>
      <div>{children}</div>
    </div>
  );
}

const DEFAULT_QA_QUESTIONS = 25;

function isValidQALimit(value: number) {
  return Number.isSafeInteger(value) && value >= 1;
}

function ProjectQA({ base, generation, onGenerating, recoveryVersion, recoveryBusy, targetQuestion, visible = true, generationRequest = 0, onGenerationRequestHandled }: GenerationProps & GenerationRequestProps & { onGenerating: (active: boolean) => void; recoveryVersion: number; recoveryBusy: boolean; targetQuestion?: string; visible?: boolean }) {
  const [saved, setSaved] = useState<SavedQA | null>(null);
  const [tagsBusy, setTagsBusy] = useState(false);
  const [tagRecovery, setTagRecovery] = useState(0);
  const [latestVersion, setLatestVersion] = useState<string | null>(null);
  const [revisionId, setRevisionId] = useState<number | null>(null);
  const [revisionLoading, setRevisionLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const generatingRef = useRef(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState('');
  const [cancelled, setCancelled] = useState(false);
  const [page, setPage] = useState(0);
  useEffect(() => { setPage(0); }, [targetQuestion]);
  const [topic, setTopic] = useState(ALL_TOPICS);
  const [tagSearch, setTagSearch] = useState('');
  useEffect(() => { setTopic(ALL_TOPICS); setTagSearch(''); setPage(0); }, [base, saved?.generated_at, revisionId, targetQuestion]);
  const path = `${base}/codex/qa`;
  // All result reads share an identity with explicit selections and mutations.
  // Cancelling transport alone is insufficient if a response already arrived.
  const viewIdentity = useRef(createEditorIdentity());
  const latestRead = useRef<AbortController | null>(null);
  const selectedRevision = useRef<number | null>(null);
  const selectingRevision = useRef(false);
  const revisionRequestIdentity = useRef<number | null>(null);
  const previousTargetQuestion = useRef(targetQuestion);
  const deletingRef = useRef(false);
  const mounted = useRef(true);
  function invalidateRead() {
    viewIdentity.current.invalidate();
    latestRead.current?.abort();
    latestRead.current = null;
    setLoading(false);
  }
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      viewIdentity.current.invalidate();
      latestRead.current?.abort();
    };
  }, []);
  const limitStorageKey = `projecthub.qa-max-questions:${base}`;
  const [maxQuestions, setMaxQuestions] = useState(() => {
    try {
      const savedLimit = Number(localStorage.getItem(limitStorageKey));
      if (isValidQALimit(savedLimit)) return String(savedLimit);
    } catch { /* Storage is optional. */ }
    return String(DEFAULT_QA_QUESTIONS);
  });
  useEffect(() => {
    // Project search refers to the current saved Q&A, not a historical version.
    if (previousTargetQuestion.current !== targetQuestion) {
      previousTargetQuestion.current = targetQuestion;
      // Search changes the view, but must not discard an in-flight mutation
      // whose result will supply the current saved Q&A.
      if (!generatingRef.current && !deletingRef.current) invalidateRead();
      selectedRevision.current = null;
      selectingRevision.current = false;
      setRevisionId(null);
      setRevisionLoading(false);
    }
    if (selectedRevision.current !== null || selectingRevision.current || generatingRef.current || deletingRef.current) return;
    const abort = new AbortController();
    latestRead.current?.abort();
    latestRead.current = abort;
    const identity = viewIdentity.current.begin();
    const current = () => !abort.signal.aborted && mounted.current && viewIdentity.current.current(identity);
    api<SavedQA | null>(path, { signal: abort.signal })
      .then((result) => {
        if (current()) {
          setSaved(result);
          setError('');
          setLatestVersion(result?.generated_at ?? null);
          setRevisionId(null);
        }
      })
      .catch((e) => { if (current()) setError(e.message); })
      .finally(() => {
        if (current()) setLoading(false);
        if (latestRead.current === abort) latestRead.current = null;
      });
    return () => { abort.abort(); };
  }, [path, recoveryVersion, targetQuestion, tagRecovery]);
  const generationDisabled = tagsBusy || loading || generating || recoveryBusy || deleting || revisionLoading;
  const validLimit = isValidQALimit(Number(maxQuestions));
  async function generate() {
    if (generationDisabled || generatingRef.current || !validLimit) return;
    invalidateRead();
    const identity = viewIdentity.current.begin();
    generatingRef.current = true;
    setGenerating(true);
    onGenerating(true);
    setError('');
    setCancelled(false);
    try {
      const result = await post(path, { confirmed: true, ...generationBody(generation), max_items: Number(maxQuestions) });
      if (!mounted.current || !viewIdentity.current.current(identity)) return;
      selectedRevision.current = null;
      setSaved(result);
      setRevisionId(null);
      setLatestVersion(result.generated_at);
      setPage(0);
    } catch (e) {
      if (!mounted.current || !viewIdentity.current.current(identity)) return;
      if (isGenerationCancelled(e)) setCancelled(true);
      else setError((e as Error).message);
    } finally {
      generatingRef.current = false;
      if (mounted.current) { setGenerating(false); onGenerating(false); }
    }
  }
  useGenerationRequest(base, generationRequest, generating || recoveryBusy || deleting || revisionLoading || tagsBusy || !validLimit ? 'skip' : loading ? 'wait' : 'ready', generate, onGenerationRequestHandled);
  async function removeQuestion(question: string) {
    if (!saved || tagsBusy || revisionId !== null || loading || generatingRef.current || recoveryBusy || revisionLoading || deleting) return;
    if (!window.confirm(`Delete “${question}” from the current Q&A? Previous versions are kept, and refreshing may generate this question again.`)) return;
    const query = new URLSearchParams({ question, generated_at: saved.generated_at });
    invalidateRead();
    const identity = viewIdentity.current.begin();
    deletingRef.current = true;
    setDeleting(true);
    setError('');
    try {
      const result = await api<SavedQA>(`${path}/items?${query}`, { method: 'DELETE' });
      if (mounted.current && viewIdentity.current.current(identity)) setSaved(result);
    } catch (e) {
      if (mounted.current && viewIdentity.current.current(identity)) setError((e as Error).message);
    } finally {
      deletingRef.current = false;
      if (mounted.current) setDeleting(false);
    }
  }
  const pageSize = 5;
  const qaId = (item: SavedQA['items'][number]) => item.id ?? item.question;
  const topics = topicChoices(saved?.items ?? [], saved?.topic_groups, qaId);
  const topicItems = targetQuestion === undefined
    ? filterTopicItems(saved?.items ?? [], topic, topics, qaId, tagSearch) : saved?.items ?? [];
  const filtered = targetQuestion === undefined ? topicItems : topicItems.filter(item => item.question === targetQuestion);
  const totalPages = Math.ceil(filtered.length / pageSize);
  const currentPage = Math.min(page, Math.max(0, totalPages - 1));
  const items = filtered.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
  const sources = citedSources(saved?.sources ?? [], items.flatMap((item) =>
    [...item.source_ids, ...(item.variants ?? []).flatMap((answer) => answer.source_ids)]));
  const numbers = new Map(sources.map((source, index) => [source.id, index + 1]));
  return (
    <>
      <section className="card project-brief" aria-busy={loading || generating || recoveryBusy || deleting}>
        <AIResultHeader title="AI Q&A" savedAt={saved?.generated_at} previous={revisionId !== null}
          freshness={saved && revisionId === null ? 'Freshness not checked' : undefined} />
        {!validLimit && <Feedback>Enter a positive whole number in Q&A details & options.</Feedback>}
        {revisionLoading && <Feedback>Loading saved Q&A version…</Feedback>}
        <ErrorBox error={error} />
        {cancelled && <Feedback>{cancelledGenerationMessage}</Feedback>}
        <details className="brief-details">
          <summary>Q&A details & options</summary>
          {saved && <SavedGeneration generation={saved.generation} />}
          <div className="toolbar">
            <label className="inline" style={{ margin: 0 }}>
              Max questions
              <input type="number" min={1} step={1} value={maxQuestions} disabled={generationDisabled}
                aria-invalid={!validLimit} style={{ width: '7em', margin: 0 }}
                onChange={(event) => {
                  const value = event.target.value;
                  setMaxQuestions(value);
                  if (isValidQALimit(Number(value))) {
                    try { localStorage.setItem(limitStorageKey, value); } catch { /* Storage is optional. */ }
                  }
                }} />
            </label>
          </div>
        <p className="brief-note">Questions and answers drawn from imported ChatGPT conversations, linked Codex sessions, accepted solutions and decisions, and saved architecture. Generation may return fewer questions.</p>
        <RevisionHistory<SavedQA> base={base} kind="qa" version={latestVersion}
          disabled={tagsBusy || loading || generating || recoveryBusy || deleting} selectedId={revisionId} onLoadingChange={active => {
            if (active) {
              invalidateRead();
              revisionRequestIdentity.current = viewIdentity.current.begin();
            }
            selectingRevision.current = active;
            setRevisionLoading(active);
          }}
          onSelect={(snapshot, id) => {
            if (revisionRequestIdentity.current === null || !viewIdentity.current.current(revisionRequestIdentity.current)) return;
            invalidateRead();
            selectedRevision.current = id;
            setSaved(snapshot);
            setRevisionId(id);
            setPage(0);
            if (id === null) setLatestVersion(snapshot?.generated_at ?? null);
          }} />
        </details>
        {saved && <TagGeneration key={`${base}:qa-tags:${saved.generated_at}:${revisionId}`} base={base} target="qa" visible={visible}
          generatedAt={saved.generated_at} itemIds={saved.items.map(qaId)} generation={generation}
          hasTags={!!saved.topic_groups?.length} disabled={revisionId !== null || loading || generating || recoveryBusy || deleting || revisionLoading}
          onBusyChange={setTagsBusy} onRecovered={() => {
            if (selectedRevision.current === null && !selectingRevision.current && !generatingRef.current && !deletingRef.current)
              setTagRecovery(value => value + 1);
          }}
          onResult={result => {
            if (!mounted.current || selectedRevision.current !== null || selectingRevision.current || generatingRef.current || deletingRef.current
                || result.generated_at !== saved.generated_at) return;
            invalidateRead();
            setSaved(previous => previous && previous.generated_at === result.generated_at
              ? { ...previous, topic_groups: result.topic_groups, topic_grouping: result.topic_grouping } : previous);
          }} />}
        {saved && revisionId !== null && <small>AI tags can be generated for the current Q&A only.</small>}
        {!!saved && <div className="knowledge-filters">
          <TopicPicker choices={topics} selected={topic} search={tagSearch} disabled={targetQuestion !== undefined} onSearch={value => { setTagSearch(value); setPage(0); }}
            ariaLabel="AI Q&A tags" onSelect={id => { setTopic(id); setPage(0); }} />
          <button className="secondary" type="button" disabled={targetQuestion !== undefined} onClick={() => { setTopic(ALL_TOPICS); setTagSearch(''); setPage(0); }}>Reset filters</button>
        </div>}
        {saved && targetQuestion !== undefined && <p className="muted">Choose Show all above to filter Q&A by tags.</p>}
        {loading && <Feedback>Loading saved Q&A…</Feedback>}
        {!loading && !error && !items.length && <Empty>{targetQuestion ? 'This question is no longer available. Search again or choose Show all.' : saved?.items.length ? 'No questions match this tag.' : saved ? 'No supported answers were found.' : 'No saved Q&A yet. Select Q&A on the Generation tab to create it.'}</Empty>}
        {totalPages > 1 && (
          <nav className="pagination" aria-label="Q&A pages">
            <button className="secondary" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</button>
            <span role="status">Page {currentPage + 1} of {totalPages}</span>
            <button className="secondary" disabled={currentPage + 1 === totalPages} onClick={() => setPage(currentPage + 1)}>Next</button>
          </nav>
        )}
        {items.map((item) => (
          <article className="brief-section" key={item.question}>
            <div className="section-title">
              <h3><AIEmphasis text={item.question} /></h3>
              <DeleteButton label={`Delete Q&A: ${item.question}`}
                disabled={tagsBusy || revisionId !== null || loading || generating || recoveryBusy || revisionLoading || deleting}
                onClick={() => void removeQuestion(item.question)} />
            </div>
            <p><AIEmphasis text={item.answer} />{briefReferences(item.source_ids, numbers, base.split('/')[2] + '-qa', sources)}</p>
            {!!item.variants?.length && (
              <details>
                <summary>Other recorded answers ({item.variants.length})</summary>
                <p>These sources differ. Review them to determine which answer applies.</p>
                {item.variants.map((answer, index) => (
                  <p key={index}><AIEmphasis text={answer.answer} />{briefReferences(answer.source_ids, numbers, base.split('/')[2] + '-qa', sources)}</p>
                ))}
              </details>
            )}
          </article>
        ))}
      </section>
      {saved && <div className="card result-support">
        <CoverageDetails savedAt={saved.generated_at} sourceCount={saved.sources.length}
          coverage={saved.coverage} diagnostics={saved.diagnostics}
          fileCoverage={<p>Q&amp;A uses saved project evidence without inspecting repository files directly.</p>} />
        {!!sources.length && <details className="brief-sources" aria-label="Q&A sources">
          <summary>Sources ({sources.length})</summary>
          <BriefSources sources={sources} prefix={base.split('/')[2] + '-qa'} />
        </details>}
      </div>}
    </>
  );
}

function ChatGPTShares({ base, notes, onImported, showHistory = true, passageNotes, compact = false }: {
  base: string;
  notes: RecordData[];
  onImported?: () => Promise<void>;
  showHistory?: boolean;
  compact?: boolean;
  passageNotes?: PassageNoteContext;
}) {
  const [adding, setAdding] = useState(false);
  const [page, setPage] = useState(0);
  const importForm = adding && onImported && (
        <Form title="Import shared chat"
          fields={[{ name: 'url', label: 'Public ChatGPT share link', required: true }]}
          saveLabel="Import" savingLabel="Importing…" onClose={() => setAdding(false)}
          onSave={async ({ url }) => {
            await post(`${base}/notes/chatgpt-share`, { url: url.trim() });
            await onImported();
            setPage(0);
          }}
        />
      );
  if (compact) return (
    <div className="source-import">
      <button type="button" className="secondary" onClick={() => setAdding(true)}>Add ChatGPT conversation</button>
      {importForm}
    </div>
  );
  const shares = notes.filter(isChatGPTShare).sort((a, b) => b.id - a.id);
  const pageCount = Math.max(1, Math.ceil(shares.length / HISTORY_PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const start = currentPage * HISTORY_PAGE_SIZE;
  if (!shares.length) return onImported ? (
    <div className="source-import">
      <span className="muted">No shared chats yet.</span>
      <button type="button" className="secondary" onClick={() => setAdding(true)}>Add ChatGPT conversation</button>
      {importForm}
    </div>
  ) : showHistory ? <p className="empty">No shared chats yet. Add conversations in Sources.</p> : null;
  return (
    <section className="card">
      <div className="section-title">
        <div>
          <h2>ChatGPT shared conversations</h2>
          <p className="muted">{onImported
            ? 'Import a public share link to save its text for Project status and AI Q&A.'
            : 'Saved transcripts. Add conversations in Sources.'}</p>
        </div>
        {onImported && <button className="secondary" onClick={() => setAdding(true)}>Import shared chat</button>}
      </div>
      {importForm}
      {!showHistory && !!shares.length && (
        <PagedItems label="Shared conversation pages">
          {shares.map((share) => (
            <article className="list-item" key={share.id}>
              <strong>{share.title || 'Untitled conversation'}</strong>
              <div className="actions left">
                <a href={chatGPTTranscriptHref({ projectId: Number(base.split('/')[2]), noteId: share.id })}>
                  Read saved transcript
                </a>
                <a href={share.reference} target="_blank" rel="noreferrer">Open shared chat</a>
              </div>
            </article>
          ))}
        </PagedItems>
      )}
      {showHistory && pageCount > 1 && (
        <nav className="pagination" aria-label="Shared chat pages">
          <button className="secondary" disabled={currentPage === 0}
            onClick={() => setPage(currentPage - 1)}>Newer chats</button>
          <span role="status">Page {currentPage + 1} of {pageCount}</span>
          <button className="secondary" disabled={currentPage === pageCount - 1}
            onClick={() => setPage(currentPage + 1)}>Older chats</button>
        </nav>
      )}
      {showHistory && passageNotes && shares.slice(start, start + HISTORY_PAGE_SIZE).map((share) => (
        <SharedChatTranscript key={share.id} base={base} share={share} passageNotes={passageNotes} />
      ))}
    </section>
  );
}

function SharedChatTranscript({ base, share, passageNotes }: {
  base: string; share: RecordData; passageNotes: PassageNoteContext;
}) {
  const [expanded, setExpanded] = useState(false);
  return <details className="history-turn" open={expanded}
    onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary>{share.title}</summary>
    {expanded && <>
      <p><a href={share.reference} target="_blank" rel="noreferrer">Open shared chat</a></p>
      <LazyNote base={base} note={share}>{loaded => (
        <SaveablePassage context={passageNotes} title={share.title ?? 'ChatGPT passage'}
          reference={chatGPTTranscriptHref({ projectId: passageNotes.projectId, noteId: share.id })}>
          <div className="shared-transcript">{loaded.content}</div>
        </SaveablePassage>
      )}</LazyNote>
    </>}
  </details>;
}

function ProjectHistory({ base, projectId, historyLink, notes, onLeaveHistory, onNoteSaved }: {
  base: string;
  projectId: number;
  historyLink: HistoryLink | null;
  notes: RecordData[];
  onLeaveHistory: () => void;
  onNoteSaved: () => Promise<void>;
}) {
  const passageNotes = { base, projectId, onSaved: onNoteSaved };
  const [sessions, setSessions] = useState<RecordData[]>([]);
  const [sessionId, setSessionId] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [indexVersion, setIndexVersion] = useState(0);
  useEffect(() => {
    let active = true;
    api<RecordData[]>(`${base}/codex/sessions`)
      .then((rows) => {
        if (!active) return;
        setSessions(rows);
        setSessionId(String(rows[0]?.id ?? ''));
      })
      .catch((e) => { if (active) setError(e.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [base]);
  return (
    <>
      <HistorySearch base={base}
        onIndexed={() => setIndexVersion((value) => value + 1)} />
      {historyLink && 'noteId' in historyLink ? (
        <IndexedChatGPTTranscript key={`${historyLink.noteId}/${historyLink.query ?? ''}`}
          note={notes.find((note) => note.id === historyLink.noteId && isChatGPTShare(note))}
          query={historyLink.query} onClose={onLeaveHistory} passageNotes={passageNotes} />
      ) : historyLink ? (
        <IndexedHistoryTurn key={`${historyLink.sessionId}/${historyLink.turnId}`}
          prefix={`${base}/codex`} target={historyLink} indexVersion={indexVersion}
          sessionTitle={sessions.find((session) => session.id === historyLink.sessionId)?.title}
          onClose={onLeaveHistory} passageNotes={passageNotes} />
      ) : (
      <>
      <ChatGPTShares base={base} notes={notes} passageNotes={passageNotes} />
      <section className="card">
        <h2>Project conversation history</h2>
        <p className="muted">Select conversation text to save it as a note. Add sessions in Sources.</p>
        <ErrorBox error={error} />
        {loading ? <Feedback>Loading project sessions…</Feedback> : (
          <label>
            Project session
            <select value={sessionId} onChange={(e) => setSessionId(e.target.value)}>
              <option value="">Select a session</option>
              {sessions.map((session) => (
                <option key={session.id} value={session.id}>
                  {sessionCaption(session)}
                </option>
              ))}
            </select>
          </label>
        )}
        {!loading && !error && !sessions.length && <Empty>No sessions associated with this project.</Empty>}
      </section>
      {sessionId && <SavedHistory key={sessionId} prefix={`${base}/codex`} sessionId={sessionId}
        passageNotes={passageNotes} sessionTitle={sessions.find((row) => String(row.id) === sessionId)?.title} />}
      </>
      )}
    </>
  );
}

function IndexedChatGPTTranscript(props: {
  note?: RecordData; query?: string; onClose: () => void; passageNotes: PassageNoteContext;
}) {
  const loaded = useLoadedNote(props.passageNotes.base, props.note ?? { id: 0 }, !!props.note);
  if (!props.note || loaded.note) return <LoadedChatGPTTranscript {...props} note={loaded.note ?? undefined} />;
  return <section className="card saved-history" aria-busy={loaded.loading}>
    <div className="section-title">
      <h2>{props.note.title || 'Saved ChatGPT transcript'}</h2>
      <button className="secondary" onClick={props.onClose}>Browse conversations</button>
    </div>
    {loaded.loading && <Feedback>Loading saved content…</Feedback>}
    <ErrorBox error={loaded.error} />
    {loaded.error && <button type="button" className="secondary" onClick={loaded.retry}>Retry loading content</button>}
  </section>;
}

function LoadedChatGPTTranscript({ note, query, onClose, passageNotes }: {
  note?: RecordData;
  query?: string;
  onClose: () => void;
  passageNotes: PassageNoteContext;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const mark = useRef<HTMLElement>(null);
  const content = String(note?.content ?? '');
  const match = transcriptMatch(content, query);
  useEffect(() => {
    const target = mark.current ?? heading.current;
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: 'center' });
  }, []);
  return (
    <section className="card saved-history">
      <div className="section-title">
        <h2 ref={heading} tabIndex={-1}>{note?.title || 'Saved ChatGPT transcript'}</h2>
        <button className="secondary" onClick={onClose}>Browse conversations</button>
      </div>
      {note ? (
        <>
          <p><a href={note.reference} target="_blank" rel="noreferrer">Open shared chat</a></p>
          <SaveablePassage context={passageNotes} title={note.title ?? 'ChatGPT passage'}
            reference={chatGPTTranscriptHref({ projectId: passageNotes.projectId, noteId: note.id })}>
          <div className="shared-transcript">
            {match ? (
              <>
                {content.slice(0, match.start)}
                <mark ref={mark} tabIndex={-1}>{content.slice(match.start, match.start + match.length)}</mark>
                {content.slice(match.start + match.length)}
              </>
            ) : content}
          </div>
          </SaveablePassage>
        </>
      ) : <ErrorBox error="This ChatGPT transcript is not available in this project." />}
    </section>
  );
}

function IndexedHistoryTurn({ prefix, target, indexVersion, sessionTitle, onClose, passageNotes }: {
  prefix: string;
  target: HistoryTurnLink;
  indexVersion: number;
  sessionTitle?: string;
  onClose: () => void;
  passageNotes: PassageNoteContext;
}) {
  const [turn, setTurn] = useState<SavedTurn | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
    heading.current?.scrollIntoView({ block: 'start' });
  }, []);
  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    setTurn(null);
    setError('');
    api<SavedTurn>(`${prefix}/sessions/${target.sessionId}/history/turn?${new URLSearchParams({ turn_id: target.turnId })}`,
      { signal: abort.signal })
      .then((result) => { if (!abort.signal.aborted) setTurn(result); })
      .catch((e) => { if (!abort.signal.aborted) setError(e.message); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [prefix, target.sessionId, target.turnId, indexVersion]);
  return (
    <section className="card saved-history" aria-busy={loading}>
      <div className="section-title">
        <div><h2 ref={heading} tabIndex={-1}>Saved conversation turn</h2><small>{sessionTitle ? `${sessionTitle} · #${target.sessionId}` : `Session #${target.sessionId}`} · Turn {target.turnId}</small></div>
        <button className="secondary" onClick={onClose}>Browse recent turns</button>
      </div>
      <ErrorBox error={error} />
      {error && <p>Open Source details above and refresh the Codex index to include newly saved turns, then this link will retry.</p>}
      {loading && <Feedback>Loading the requested turn…</Feedback>}
      {turn && (
        <>
          <Badge value={turn.status} />
          <SaveablePassage context={passageNotes} title={sessionTitle ? `Passage from ${sessionTitle}` : `Passage from session #${target.sessionId}`}
            reference={historyTurnHref(target)}>
          {turn.messages.map((message, index) => (
            <div className="history-message" key={index}>
              <small>{message.role === 'user' ? 'USER' : 'CODEX'}</small>
              <HistoryMarkdown text={message.text} role={message.role} />
            </div>
          ))}
          </SaveablePassage>
        </>
      )}
    </section>
  );
}

type SavedTurn = {
  id: string;
  status: string;
  messages: { role: 'user' | 'assistant'; text: string }[];
  shared_history?: { session_id: number; turn_id: string; session_title?: string };
};

const HISTORY_PAGE_SIZE = 3;

function SavedHistory({
  prefix,
  sessionId,
  passageNotes,
  sessionTitle,
}: {
  prefix: string;
  sessionId: string;
  passageNotes: PassageNoteContext;
  sessionTitle?: string;
}) {
  const [turns, setTurns] = useState<SavedTurn[]>([]);
  const [cursorTrail, setCursorTrail] = useState<(string | null)[]>([null]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const request = useRef<AbortController | null>(null);

  const load = useCallback(
    async (trail: (string | null)[] = [null]) => {
      request.current?.abort();
      const controller = new AbortController();
      request.current = controller;
      setLoading(true);
      setError('');
      try {
        const cursor = trail[trail.length - 1];
        const query = new URLSearchParams({ limit: String(HISTORY_PAGE_SIZE) });
        if (cursor) query.set('cursor', cursor);
        const page = await api<{
          turns: SavedTurn[];
          next_cursor: string | null;
        }>(`${prefix}/sessions/${sessionId}/history?${query}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setTurns(page.turns);
        setCursorTrail(trail);
        setNextCursor(page.next_cursor);
      } catch (e) {
        if (!controller.signal.aborted) setError((e as Error).message);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    },
    [prefix, sessionId],
  );

  useEffect(() => {
    load();
    return () => {
      request.current?.abort();
    };
  }, [load]);

  const historyGroups = groupSharedHistory(turns, true);
  const renderTurn = (turn: SavedTurn) => (
    <article className="history-turn" key={turn.id}>
      <div className="section-title">
        <h3>Turn {turn.id}</h3>
        <Badge value={turn.status} />
      </div>
      {turn.shared_history && (
        <p className="muted">Shared with{' '}
          <a href={historyTurnHref({
            projectId: passageNotes.projectId,
            sessionId: turn.shared_history.session_id,
            turnId: turn.shared_history.turn_id,
          })}>
            {turn.shared_history.session_title
                  ? `${turn.shared_history.session_title} · #${turn.shared_history.session_id}`
                  : `Session #${turn.shared_history.session_id}`}
          </a>
        </p>
      )}
      <SaveablePassage context={passageNotes} title={sessionTitle ? `Passage from ${sessionTitle}` : `Passage from session #${sessionId}`}
        reference={historyTurnHref({ projectId: passageNotes.projectId, sessionId: Number(sessionId), turnId: turn.id })}>
      {turn.messages.map((message, index) => (
        <div className="history-message" key={`${turn.id}-${index}`}>
          <small>{message.role === 'user' ? 'USER' : 'CODEX'}</small>
          <HistoryMarkdown text={message.text} role={message.role} />
        </div>
      ))}
      </SaveablePassage>
      {!turn.messages.length && (
        <p className="muted">No user or assistant text in this turn.</p>
      )}
    </article>
  );

  return (
    <section className="card saved-history">
      <div className="section-title">
        <div>
          <h2>Saved conversation history</h2>
          <p className="muted">
            Read from local Codex history without resuming the session or
            sending a model request.
          </p>
        </div>
        <button className="secondary" disabled={loading} onClick={() => load()}>
          Refresh history
        </button>
      </div>
      <ErrorBox error={error} />
      {(nextCursor || cursorTrail.length > 1) && (
        <nav className="pagination" aria-label="Saved conversation pages">
          <button
            className="secondary"
            disabled={loading || cursorTrail.length === 1}
            onClick={() => load(cursorTrail.slice(0, -1))}
          >
            Newer turns
          </button>
          <span role="status">{loading ? 'Loading page…' : `Page ${cursorTrail.length}`}</span>
          <button
            className="secondary"
            disabled={loading || !nextCursor}
            onClick={() => nextCursor && load([...cursorTrail, nextCursor])}
          >
            Older turns
          </button>
        </nav>
      )}
      {historyGroups.map((group) => group.kind === 'shared' ? (
        <details className="history-turn" key={`shared-${sessionId}-${group.turns[0].id}`}>
          <summary>Shared history — {group.turns.length} {group.turns.length === 1 ? 'turn' : 'turns'} on this page</summary>
          {group.turns.map(renderTurn)}
        </details>
      ) : renderTurn(group.turn))}
      {!turns.length && !loading && !error && (
        <Empty>No saved conversation turns found.</Empty>
      )}
      {loading && !turns.length && <Feedback>Loading saved history…</Feedback>}
    </section>
  );
}

function Codex({ base, onError, notes, onImported, onUnlinked, onOpenGeneration, targetSessionId }: {
  base: string;
  onError: (message: string) => void;
  notes: RecordData[];
  onImported: () => Promise<void>;
  onUnlinked: (sessionId: number) => Promise<void>;
  onOpenGeneration: () => void;
  targetSessionId?: number;
}) {
  const [sessionRows, setSessions] = useState<RecordData[] | null>(null);
  const [sessionId, setSessionId] = useState('');
  useEffect(() => { if (targetSessionId !== undefined) setSessionId(String(targetSessionId)); }, [targetSessionId, sessionRows]);
  const [threadId, setThreadId] = useState('');
  const [linkTitle, setLinkTitle] = useState('');
  const [linkFeedback, setLinkFeedback] = useState<{ tone: 'success' | 'error'; message: string } | null>(null);
  const [sessionFilter, setSessionFilter] = useState('all');
  const [sessionOrder, setSessionOrder] = useState('recent');
  const [renaming, setRenaming] = useState<RecordData | null>(null);
  const [unlinkedSession, setUnlinkedSession] = useState('');
  const [busy, setBusy] = useState(false);
  const prefix = `${base}/codex`;
  const mounted = useRef(true);
  const refreshVersion = useRef(0);
  const sessions = sessionRows ?? [];
  const chatCount = notes.filter(isChatGPTShare).length;
  const selectedSession = sessions.find((s) => String(s.id) === sessionId);
  const readingOnly = selectedSession?.status === 'linked';
  const filteredSessions = [...sessions].filter((session) => {
    return (targetSessionId !== undefined ? session.id === targetSessionId : sessionFilter === 'all' || (sessionFilter === 'linked'
        ? session.status === 'linked' : session.status !== 'linked'));
  }).sort((a, b) => (sessionOrder === 'topic' ? (Number(!a.topic) - Number(!b.topic)
    || String(a.topic ?? '').localeCompare(String(b.topic ?? ''), undefined, { sensitivity: 'base' })) : 0)
    || String(sessionActivity(b)).localeCompare(String(sessionActivity(a)))
    || b.id - a.id);
  const refresh = useCallback(async () => {
    const version = ++refreshVersion.current;
    const current = () => mounted.current && version === refreshVersion.current;
    try {
      const rows = await api<RecordData[]>(`${prefix}/sessions`);
      if (current()) {
        setSessions(rows);
        setSessionId((selected) =>
          rows.some((session) => String(session.id) === selected) ? selected : String(rows[0]?.id ?? ''),
        );
      }
    } catch (error) {
      if (current()) throw error;
    }
  }, [prefix]);
  useEffect(() => {
    mounted.current = true;
    refresh()
      .catch((e) => {
        if (mounted.current) onError(e.message);
      });
    return () => {
      mounted.current = false;
    };
  }, [refresh, onError]);
  async function action(fn: () => Promise<unknown>, after?: () => Promise<void>) {
    setBusy(true);
    onError('');
    try {
      await fn();
      await Promise.all([refresh(), after?.()]);
    } catch (e) {
      if (mounted.current) onError((e as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  async function linkSession() {
    const id = threadId.trim();
    const title = linkTitle.trim();
    if (busy || !id) return;
    setLinkFeedback(null);
    onError('');
    setBusy(true);
    try {
      const linked = await post(`${prefix}/sessions`, { thread_id: id, ...(title ? { title } : {}) }) as RecordData;
      if (!mounted.current) return;
      setSessions((current) => [linked, ...(current ?? []).filter((session) => session.id !== linked.id)]);
      setSessionId(String(linked.id));
      setThreadId('');
      setLinkTitle('');
      setLinkFeedback({ tone: 'success', message: 'Codex session linked.' });
      try {
        await refresh();
      } catch {
        if (mounted.current) setLinkFeedback({ tone: 'success', message: 'Codex session linked. The session list could not be refreshed.' });
      }
    } catch (error) {
      if (mounted.current) setLinkFeedback({ tone: 'error', message: (error as Error).message });
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <>
      <div>
        <section className="card">
          <div className="section-title">
            <div>
              <h2>Project sessions</h2>
              <small>
                {sessionRows && `${sessions.length.toLocaleString()} linked Codex session${sessions.length === 1 ? '' : 's'} · `}
                {chatCount.toLocaleString()} imported conversation{chatCount === 1 ? '' : 's'}
              </small>
            </div>
          </div>
          {unlinkedSession && <Feedback tone="success">
            <p>Unlinked {unlinkedSession}. Use Generation to retry unfinished results with the updated sources.</p>
            <button type="button" className="secondary" disabled={busy} onClick={onOpenGeneration}>Open Generation</button>
          </Feedback>}
          <div className="source-toolbar">
            <ChatGPTShares base={base} notes={notes} onImported={onImported} compact />
            <details className="source-link">
              <summary className="button secondary">Link Codex session</summary>
          <label>
            Session name (optional)
            <input value={linkTitle} maxLength={200} disabled={busy}
              onChange={(event) => setLinkTitle(event.target.value)}
              placeholder="Use the Codex session name" />
          </label>
          <label>
            Codex thread ID
            <input
              value={threadId}
              disabled={busy}
              onChange={(e) => { setThreadId(e.target.value); setLinkFeedback(null); }}
              placeholder="Thread ID to read saved conversation history"
            />
          </label>
          <button
            className="secondary"
            disabled={busy || !threadId.trim()}
            onClick={() => void linkSession()}
          >
            Add session for reading
          </button>
          {linkFeedback && <Feedback tone={linkFeedback.tone}>
            <p>{linkFeedback.message}</p>
            {linkFeedback.tone === 'error' && <p>For connection issues, open Settings → AI profiles.</p>}
          </Feedback>}
            </details>
          </div>
          <div className="source-filters">
          <label>
            Sessions
            <select value={sessionFilter} onChange={(e) => setSessionFilter(e.target.value)}>
              <option value="all">All sessions</option>
              <option value="linked">Linked for reading</option>
              <option value="managed">Discussion sessions</option>
            </select>
          </label>
          <label>
            Organize by
            <select value={sessionOrder} onChange={(e) => setSessionOrder(e.target.value)}>
              <option value="recent">Recent activity</option>
              <option value="topic">Topic, then recent activity</option>
            </select>
          </label>
          </div>
          <PagedItems key={JSON.stringify([sessionFilter, sessionOrder])} label="Project session pages">
            {filteredSessions.map((session) => (
              <article className="list-item session-row" key={session.id}>
                <div className="record-row">
                  <label className="session-choice record-main">
                    <input type="radio" name="source-session" value={session.id} disabled={busy}
                      checked={String(session.id) === sessionId}
                      onChange={() => setSessionId(String(session.id))} />
                    <strong>{session.title || 'Untitled session'}</strong>
                  </label>
                  <div className="record-actions">
                    <EditButton label={`Edit session: ${sessionCaption(session)}`} disabled={busy}
                      onClick={() => setRenaming(session)} />
                  </div>
                </div>
                <div className="session-metadata">
                  <small>#{session.id}{session.topic ? ` · ${session.topic}` : ''} · {label(session.status ?? '')} · {session.codex_account || 'Launch account'}</small>
                  <small>{session.codex_activity_at ? 'Codex last updated' : 'Last activity recorded here'}: {
                    sessionActivity(session) ? new Date(sessionActivity(session)).toLocaleString() : 'Unknown'
                  }</small>
                </div>
                <SessionIdentifier threadId={String(session.thread_id ?? '')} />
                <div className="session-actions">
                <button className="quiet" disabled={busy} onClick={() => void action(() =>
                  patch(`${prefix}/sessions/${session.id}/sync`, { enabled: session.auto_sync === false }))}>
                  {session.auto_sync === false ? 'Resume automatic sync' : 'Pause automatic sync'}
                </button>
                {' · '}
                <button className="quiet" disabled={busy || session.status === 'running'} onClick={() => {
                  if (window.confirm(`Unlink “${session.title || `Session #${session.id}`}”? The Codex thread and saved records will be kept.`)) {
                    void action(() => patch(`${prefix}/sessions/${session.id}/link`, { linked: false }), () => {
                      if (mounted.current) setUnlinkedSession(sessionCaption(session));
                      return onUnlinked(session.id);
                    });
                  }
                }}>Unlink</button>
                </div>
              </article>
            ))}
          </PagedItems>
          {sessionRows && !filteredSessions.length && <Empty>{sessions.length ? 'No matching sessions.' : 'No sessions added yet.'}</Empty>}
          {renaming && (
            <Form title="Edit session" fields={[{ ...titleField, maxLength: 200 },
              { name: 'topic', label: 'Topic (optional)', maxLength: 80 }]} initial={renaming}
              onClose={() => setRenaming(null)}
              onSave={async ({ title, topic }) => {
                await patch(`${prefix}/sessions/${renaming.id}`, { title: String(title).trim(), topic: String(topic).trim() });
                await refresh();
              }} />
          )}
          {selectedSession && <p>Selected: <strong>{selectedSession.title}</strong> · #{selectedSession.id}</p>}
          {readingOnly && (
            <p className="muted">
              This session is linked for reading only. Resume selected explicitly
              to ask it a question in Reviews.
            </p>
          )}


          <div className="actions left">
            <button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  const session = await post(`${prefix}/sessions`);
                  if (mounted.current) setSessionId(String(session.id));
                })
              }
            >
              Start read-only session
            </button>
            <button
              className="secondary"
              disabled={busy || !sessionId}
              onClick={() =>
                action(() => post(`${prefix}/sessions/${sessionId}/resume`))
              }
            >
              Resume selected
            </button>
          </div>
        </section>
      </div>
    </>
  );
}

const staleReviewContextMessage = 'Project context changed since it was previewed. Preview it again before sending.';

function CodexReview({ base, generation, onNotesChanged, onManageSessions }: {
  base: string;
  generation: Generation;
  onNotesChanged: () => Promise<void>;
  onManageSessions: () => void;
}) {
  const prefix = `${base}/codex`;
  const sessionStorageKey = `projecthub.review-session:${base}`;
  const [sessions, setSessions] = useState<RecordData[] | null>(null);
  const [sessionId, setSessionId] = useState('');
  const [renaming, setRenaming] = useState<RecordData | null>(null);
  const [prompt, setPrompt] = useState('');
  const [context, setContext] = useState<{ content: string; version: string } | null>(null);
  const [contextVisible, setContextVisible] = useState(false);
  const [contextNeedsPreview, setContextNeedsPreview] = useState(false);
  const [events, setEvents] = useState<any>(null);
  const responseVersion = useRef(0);
  const [eventsError, setEventsError] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const actionInFlight = useRef(false);
  const [cancelRequested, setCancelRequested] = useState<number | null>(null);
  const [waiting, setWaiting] = useState<{ id: number; sessionId: string; generation: Generation } | null>(null);
  const autoRetried = useRef<number | null>(null);
  const [responsePage, setResponsePage] = useState(0);
  const selectedSession = sessions?.find((session) => String(session.id) === sessionId);
  const canSend = !!selectedSession && selectedSession.status !== 'linked';
  const completed = completedMessages(events?.response_events ?? []).reverse();
  const responsePageCount = Math.max(1, Math.ceil((events?.response_total ?? 0) / HISTORY_PAGE_SIZE));
  const currentResponsePage = Math.min(responsePage, responsePageCount - 1);
  const newestResponseId = events?.latest_response_id;
  useEffect(() => { setResponsePage(0); }, [sessionId, newestResponseId]);
  useEffect(() => {
    if (events && responsePage >= responsePageCount) setResponsePage(responsePageCount - 1);
  }, [events, responsePage, responsePageCount]);
  const latestRequest = (events?.deliveries ?? []).findLast((item: RecordData) => item.decision_id == null);
  const activeReview = (events?.deliveries ?? []).findLast((item: RecordData) =>
    item.decision_id == null && item.status === 'sent' && events?.session?.status === 'running');
  const queuedReview = latestRequest?.status === 'queued' ? latestRequest : null;
  const cancelTargets: RecordData[] = [activeReview, queuedReview].filter(Boolean);


  useEffect(() => {
    const abort = new AbortController();
    api<RecordData[]>(`${prefix}/sessions`, { signal: abort.signal })
      .then((rows) => {
        if (abort.signal.aborted) return;
        setSessions(rows);
        let savedId: string | null = null;
        try { savedId = localStorage.getItem(sessionStorageKey); } catch { /* Storage is optional. */ }
        const selected = rows.find((row) => String(row.id) === savedId)
          ?? rows.filter((row) => row.status !== 'linked').at(-1)
          ?? rows.at(-1);
        setSessionId(String(selected?.id ?? ''));
      })
      .catch((e) => { if (!abort.signal.aborted) setError(e.message); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [prefix, sessionStorageKey]);

  async function action(fn: () => Promise<unknown>) {
    if (actionInFlight.current) return;
    actionInFlight.current = true;
    setBusy(true);
    setError('');
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      actionInFlight.current = false;
      setBusy(false);
    }
  }

  async function previewContext() {
    const result = await api<{ content: string; version: string }>(`${prefix}/context`);
    if (typeof result.content !== 'string' || !/^[0-9a-f]{64}$/.test(result.version)) {
      throw new Error('The project context preview could not be verified. Try previewing it again.');
    }
    setContext(result);
    setContextVisible(true);
    setContextNeedsPreview(false);
  }

  async function cancelReview(id: number) {
    if (busy || cancelRequested === id) return;
    if (waiting?.id === id) setWaiting(null);
    autoRetried.current = id;
    setCancelRequested(id);
    await action(async () => {
      try {
        const updated: RecordData = await post(`${prefix}/deliveries/${id}/cancel`);
        responseVersion.current += 1;
        setEvents((current: any) => current && ({
          ...current,
          deliveries: current.deliveries.map((item: RecordData) => item.id === id ? updated : item),
        }));
        if (!updated.cancel_requested) setCancelRequested(null);
      } catch (error) {
        setCancelRequested(null);
        throw error;
      }
    });
  }

  function selectReviewSession(id: string) {
    setWaiting(null);
    setCancelRequested(null);
    setEvents(null);
    setEventsError('');
    setSessionId(id);
    try { localStorage.setItem(sessionStorageKey, id); } catch { /* Storage is optional. */ }
  }

  async function ask(question: string, fresh = false) {
    if (contextNeedsPreview) throw new Error(staleReviewContextMessage);
    let target = sessionId;
    let request: RecordData;
    try {
      if (fresh || !canSend) {
        if (context) {
          const current = await api<{ version: string }>(`${prefix}/context`);
          if (current.version !== context.version) throw new Error(staleReviewContextMessage);
        }
        const name = Array.from(question.replace(/\s+/g, ' ').trim());
        const title = name.length > 72 ? name.slice(0, 71).join('').trimEnd() + '…' : name.join('');
        const session: RecordData = await post(`${prefix}/sessions`, { title });
        target = String(session.id);
        setSessions((rows) => [...(rows ?? []), session]);
        selectReviewSession(target);
      }
      request = await post(`${prefix}/sessions/${target}/turns`, {
        prompt: question, ...generationBody(generation),
        ...(context ? { context_version: context.version } : {}),
      });
    } catch (error) {
      if (error instanceof Error && error.message === staleReviewContextMessage) {
        setContext(null);
        setContextVisible(false);
        setContextNeedsPreview(true);
      }
      throw error;
    }
    setContext(null);
    setContextVisible(false);
    if (request.status === 'queued') {
      setWaiting({ id: request.id, sessionId: target, generation });
    }
    if (!fresh) setPrompt('');
  }
  useEffect(() => {
    if (busy || !waiting || waiting.sessionId !== sessionId ||
        String(events?.session?.id) !== sessionId) return;
    const request = events.deliveries?.find((item: RecordData) => item.id === waiting.id);
    if (!request) return;
    if (request.status !== 'queued') {
      setWaiting(null);
      return;
    }
    if (events.session.status === 'running' || events.session.status === 'linked' ||
        autoRetried.current === waiting.id) return;
    autoRetried.current = waiting.id;
    setWaiting(null);
    void action(() => post(`${prefix}/deliveries/${waiting.id}/send`, {
      confirmed: true, ...generationBody(waiting.generation),
    }));
  }, [busy, events, prefix, sessionId, waiting]);
  useEffect(() => {
    if (!sessionId) return;
    return startReviewPolling(async (signal) => {
      const version = responseVersion.current;
      const [result, responses] = await Promise.all([
        api<any>(`${prefix}/sessions/${sessionId}/events`, { signal }),
        api<{ events: any[]; total: number; latest_id: number | null }>(
          `${prefix}/sessions/${sessionId}/responses?offset=${responsePage * HISTORY_PAGE_SIZE}&limit=${HISTORY_PAGE_SIZE}`,
          { signal }),
      ]);
      return { version, result: { ...result, response_events: responses.events,
        response_total: responses.total, latest_response_id: responses.latest_id, response_page: responsePage } };
    }, ({ version, result }) => {
      if (version === responseVersion.current) {
        setEvents(result);
        setEventsError('');
      }
    }, (error) => setEventsError((error as Error).message));
  }, [prefix, sessionId, responsePage]);
  return (
    <>
      <section className="card">
        <div className="section-title">
          <h2>Discussion session</h2>
          <div className="note-actions">
            {selectedSession && <EditButton label="Rename selected session" disabled={busy}
              onClick={() => setRenaming(selectedSession)} />}
            <button className="secondary" onClick={onManageSessions}>Manage sources</button>
          </div>
        </div>
        {renaming && (
          <Form title="Rename session" fields={[{ ...titleField, maxLength: 200 }]} initial={renaming}
            onClose={() => setRenaming(null)}
            onSave={async ({ title }) => {
              const updated: RecordData = await patch(`${prefix}/sessions/${renaming.id}`, { title: String(title).trim() });
              setSessions((rows) => rows?.map((row) => row.id === updated.id ? { ...row, title: updated.title } : row) ?? null);
            }} />
        )}
        <ErrorBox error={error} />
        {loading ? <Feedback>Loading sessions…</Feedback> : sessions?.length ? (
          <label>
            Review session
            <select value={sessionId} disabled={busy} onChange={(e) => selectReviewSession(e.target.value)}>
              {sessions.map((session) => (
                <option key={session.id} value={session.id}>
                  {sessionCaption(session)}
                  {session.status === 'linked' ? ' · Reading only' : ''}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {sessions && (
          <details>
            <summary>Ask a question</summary>
            <form onSubmit={(e) => {
              e.preventDefault();
              const question = prompt.trim();
              if (busy || waiting || contextNeedsPreview || !question) return;
              void action(() => ask(question));
            }}>
              <label>
                Project question
                <textarea rows={3} required maxLength={20000} value={prompt} readOnly={busy}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="What should we do next, based on this project's history?" />
              </label>
              <p className="muted">This sends your question and project context to Codex for a read-only response.</p>
              <div className="actions left">
                <button disabled={busy || !!waiting || contextNeedsPreview || !prompt.trim()}>
                  {busy ? 'Working…' : canSend ? 'Ask Codex' : 'Ask in a new review session'}
                </button>
                <button type="button" className="quiet" disabled={busy} aria-expanded={contextVisible}
                  onClick={() => {
                    if (context) setContextVisible(!contextVisible);
                    else void action(previewContext);
                  }}>
                  {contextVisible ? 'Hide context' : 'Preview context'}
                </button>
              </div>
              {contextVisible && context && <pre className="json-report">{context.content}</pre>}
            </form>
          </details>
        )}
        {!!cancelTargets.length && <div className="actions left">
          {cancelTargets.map((item) => (
            <button type="button" className="secondary" key={item.id}
              disabled={busy || cancelRequested === item.id}
              onClick={() => void cancelReview(item.id)}>
              {cancelRequested === item.id ? 'Cancelling…' : item.status === 'queued'
                ? 'Cancel queued question' : 'Cancel current response'}
            </button>
          ))}
        </div>}
        {waiting && <Feedback>Waiting for Codex to finish. Keep Reviews open to send this question automatically.</Feedback>}
      </section>
      {sessionId && (
        <section className="card">
          <div className="section-title">
            <h2>Saved Codex responses</h2>
            {latestRequest && latestRequest.status !== 'completed' && <Badge value={latestRequest.status} />}
          </div>
          <ErrorBox error={eventsError} />
          {events && events.response_page === responsePage ? (
            <>
              {completed.map((message) => (
                <article className="answer" key={message.id}>
                  <div className="section-title">
                    <div>
                      <h3>Response #{message.id}</h3>
                      {message.created_at && <time className="muted" dateTime={message.created_at}>
                        {new Date(message.created_at).toLocaleString()}
                      </time>}
                    </div>
                    <div className="note-actions">
                      <Badge value="unverified" />
                      <DeleteButton label={`Delete saved Codex response #${message.id}`} disabled={busy}
                        onClick={() => {
                          if (!window.confirm(`Delete saved Codex response #${message.id} from ProjectHub? The original Codex session is kept.`)) return;
                          void action(async () => {
                            await api(`${prefix}/sessions/${sessionId}/responses/${message.id}`, { method: 'DELETE' });
                            responseVersion.current += 1;
                            setEvents((current: any) => current && ({
                              ...current,
                              events: current.events.filter((event: RecordData) => event.id !== message.id),
                              response_events: current.response_events.filter((event: RecordData) => event.id !== message.id),
                              response_total: Math.max(0, current.response_total - 1),
                            }));
                          });
                        }} />
                    </div>
                  </div>
                  {message.question && <div className="review-question">
                    <h4>Question</h4>
                    <p>{message.question}</p>
                  </div>}
                  <Markdown>{message.text}</Markdown>
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() =>
                      action(async () => {
                        await post(`${base}/notes`, {
                          title: `Codex proposal · session ${sessionId} · event ${message.id}`,
                          content: message.text,
                          kind: 'proposal',
                          reference: `Codex session ${sessionId}, event ${message.id}`,
                        });
                        await onNotesChanged();
                      })
                    }
                  >
                    Save as proposed note
                  </button>
                </article>
              ))}
              {responsePageCount > 1 && (
                <nav className="pagination" aria-label="Saved review response pages">
                  <button className="secondary" disabled={currentResponsePage === 0}
                    onClick={() => setResponsePage(currentResponsePage - 1)}>Newer responses</button>
                  <span role="status">Page {currentResponsePage + 1} of {responsePageCount}</span>
                  <button className="secondary" disabled={currentResponsePage === responsePageCount - 1}
                    onClick={() => setResponsePage(currentResponsePage + 1)}>Older responses</button>
                </nav>
              )}
              {!completed.length && <Empty>No completed responses yet.</Empty>}
              <details>
                <summary>Request history</summary>
                <PagedItems key={sessionId} label="Request history pages">{[...(events.deliveries ?? [])].reverse().map((item: RecordData) => {
                  const content = String(item.content);
                  const question = content.replace(/^[\s\S]*?\nEND PROJECT REFERENCE DATA\nUSER REQUEST:\n/, '');
                  return (
                    <article className="delivery-preview" key={item.id}>
                      <div className="section-title">
                        <h3>Request #{item.id}</h3>
                        <Badge value={item.status} />
                      </div>
                      <Markdown>{item.decision_id == null ? question : content}</Markdown>
                      {item.error && <ErrorBox error={item.error} />}
                      {item.decision_id == null && ['draft', 'queued', 'failed'].includes(item.status ?? '') && (
                        <div className="actions left">
                          <button
                            disabled={busy || !canSend || waiting?.id === item.id}
                            onClick={() =>
                              action(async () => {
                                await post(`${prefix}/deliveries/${item.id}/send`, {
                                  confirmed: true,
                                  ...generationBody(generation),
                                });
                              })
                            }
                          >
                            Approve and retry this request
                          </button>
                          {question !== content && (
                            <button className="secondary" disabled={busy || !!waiting || contextNeedsPreview}
                              onClick={() => void action(() => ask(question, true))}>
                              Ask in a new session
                            </button>
                          )}
                        </div>
                      )}
                      {item.status === 'unverified' && (
                        <p>
                          Receipt is uncertain. Inspect session events before creating
                          any replacement request.
                        </p>
                      )}
                    </article>
                  );
                })}</PagedItems>
                <details>
                  <summary>Diagnostics</summary>
                  <Report value={events} />
                </details>
              </details>
            </>
          ) : (
            <Empty>
              {eventsError ? 'Retrying automatically…' : 'Loading saved responses…'}
            </Empty>
          )}
        </section>
      )}
    </>
  );
}
