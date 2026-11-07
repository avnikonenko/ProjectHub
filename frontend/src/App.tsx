import { Children, useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { api, patch, post } from './api';
import { completedMessages } from './codex';
import { GenerationStatus, SavedGeneration, type GenerationProvenance } from './GenerationStatus';
import { RevisionHistory } from './RevisionHistory';
import {
  chatGPTTranscriptHref, historyTurnHref, readHistoryLink, transcriptMatch, type HistoryLink, type HistoryTurnLink,
} from './historyLinks';
import { HistorySearch } from './HistorySearch';
import type { Overview, Project, RecordData } from './types';

type Field = {
  name: string;
  label: string;
  type?: 'text' | 'textarea' | 'select';
  options?: string[];
  required?: boolean;
  maxLength?: number;
  rows?: number;
};
const titleField: Field = { name: 'title', label: 'Title', required: true };
const descriptionField: Field = {
  name: 'description',
  label: 'Description',
  type: 'textarea',
};
const label = (value: string) => value === 'ai_note' ? 'AI note' : value.replaceAll('_', ' ');
const isChatGPTShare = (note: RecordData) => note.kind === 'document'
  && typeof note.reference === 'string'
  && /^https:\/\/chatgpt\.com\/share\/[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(note.reference);

function Badge({ value = '' }: { value?: string }) {
  return <span className={`badge ${value}`}>{label(value)}</span>;
}
function EditButton({ label, onClick, disabled }: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button type="button" className="icon-edit" aria-label={label} title={label}
      onClick={onClick} disabled={disabled}>
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
        strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="m14 6 4 4M4 20l4.5-1 11-11a2.8 2.8 0 0 0-4-4l-11 11L4 20Z" />
      </svg>
    </button>
  );
}
function Markdown({ children }: { children?: string }) {
  return children ? (
    <div className="markdown">
      <ReactMarkdown
        components={{
          img: ({ alt }) => <span>[Image: {alt || 'image'}]</span>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  ) : (
    <span className="muted">None recorded.</span>
  );
}
function ErrorBox({ error }: { error: string }) {
  return error ? (
    <div className="error" role="alert">
      {error}
    </div>
  ) : null;
}

function ThemeToggle() {
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
function PagedItems({ children, label, listClass, pageSize = 3 }: {
  children: React.ReactNode;
  label: string;
  listClass?: string;
  pageSize?: number;
}) {
  const [page, setPage] = useState(0);
  const items = Children.toArray(children);
  const count = Math.max(1, Math.ceil(items.length / pageSize));
  const current = Math.min(page, count - 1);
  const visible = items.slice(current * pageSize, (current + 1) * pageSize);
  return (
    <>
      {listClass ? <ul className={listClass}>{visible}</ul> : visible}
      {count > 1 && (
        <nav className="pagination" aria-label={label}>
          <button type="button" className="secondary" disabled={current === 0}
            onClick={() => setPage(current - 1)}>Previous</button>
          <span role="status">Page {current + 1} of {count}</span>
          <button type="button" className="secondary" disabled={current === count - 1}
            onClick={() => setPage(current + 1)}>Next</button>
        </nav>
      )}
    </>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}

function Form({
  title,
  fields,
  initial = {},
  onSave,
  onClose,
  saveLabel = 'Save',
  savingLabel = 'Saving…',
}: {
  title: string;
  fields: Field[];
  initial?: Record<string, any>;
  onSave: (data: Record<string, any>) => Promise<unknown>;
  onClose: () => void;
  saveLabel?: string;
  savingLabel?: string;
}) {
  const [values, setValues] = useState<Record<string, any>>(() =>
    Object.fromEntries(
      fields.map((f) => [
        f.name,
        initial[f.name] ??
          (
            {
              priority: 'medium',
              category: 'technical',
              source: 'user',
            } as Record<string, string>
          )[f.name] ??
          f.options?.[0] ??
          '',
      ]),
    ),
  );
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  return (
    <div className="modal-backdrop">
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="section-title">
          <h2>{title}</h2>
          <button
            className="quiet"
            disabled={saving}
            onClick={onClose}
            aria-label="Close form"
          >
            ×
          </button>
        </div>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            setSaving(true);
            setError('');
            try {
              await onSave(values);
              onClose();
            } catch (e) {
              setError(String((e as Error).message));
            } finally {
              setSaving(false);
            }
          }}
        >
          {fields.map((f) => (
            <label key={f.name}>
              {f.label}
              {f.required ? ' *' : ''}
              {f.type === 'textarea' ? (
                <textarea
                  value={values[f.name]}
                  required={f.required}
                  rows={f.rows ?? 4}
                  onChange={(e) =>
                    setValues({ ...values, [f.name]: e.target.value })
                  }
                />
              ) : f.type === 'select' ? (
                <select
                  value={values[f.name]}
                  onChange={(e) =>
                    setValues({ ...values, [f.name]: e.target.value })
                  }
                >
                  {f.options?.map((o) => (
                    <option key={o} value={o}>
                      {label(o)}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  value={values[f.name]}
                  required={f.required}
                  maxLength={f.maxLength}
                  onChange={(e) =>
                    setValues({ ...values, [f.name]: e.target.value })
                  }
                />
              )}
            </label>
          ))}
          <ErrorBox error={error} />
          <div className="actions">
            <button
              type="button"
              className="secondary"
              disabled={saving}
              onClick={onClose}
            >
              Cancel
            </button>
            <button disabled={saving}>{saving ? savingLabel : saveLabel}</button>
          </div>
        </form>
      </section>
    </div>
  );
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
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [adding, setAdding] = useState(false);
  const [loading, setLoading] = useState(true);
  const [historyLink, setHistoryLink] = useState(() => readHistoryLink(window.location.hash));
  const fetchProjects = useCallback(async () => {
    const data = await api<Project[]>('/projects');
    setProjects(data);
    setSelected((current) =>
      current !== null && data.some((p) => p.id === current)
        ? current
        : (data[0]?.id ?? null),
    );
  }, []);
  useEffect(() => {
    fetchProjects()
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [fetchProjects]);
  useEffect(() => {
    const update = () => setHistoryLink(readHistoryLink(window.location.hash));
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);
  const linkedProjectExists = !!historyLink && projects.some((p) => p.id === historyLink.projectId);
  const selectedProject = linkedProjectExists ? historyLink.projectId : selected;
  function selectProject(projectId: number) {
    setSelected(projectId);
    if (historyLink) {
      window.history.pushState(null, '', window.location.pathname + window.location.search);
      setHistoryLink(null);
    }
  }
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-icon">P</span>
          <div>
            ProjectHub<small>Project workspace</small>
          </div>
        </div>
        <div className="sidebar-label">YOUR PROJECTS</div>
        <div className="project-list">
          {projects.map((p) => (
            <button
              key={p.id}
              className={`project-option ${p.id === selectedProject ? 'selected' : ''}`}
              onClick={() => selectProject(p.id)}
            >
              <span className="project-dot" />
              {p.name}
            </button>
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
            Codex requests go to OpenAI.
          </small>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <span>PROJECT WORKSPACE</span>
          <div className="topbar-actions">
            <span className="local-label">● localhost</span>
            <ThemeToggle />
          </div>
        </header>
        <ErrorBox error={error} />
        {!loading && historyLink && !linkedProjectExists && (
          <ErrorBox error="The project in this history link is not available." />
        )}
        {loading ? (
          <Empty>Loading projects…</Empty>
        ) : selectedProject === null ? (
          <main>
            <div className="page-heading">
              <h1>A clear place for project work.</h1>
              <p>
                Keep conversations, decisions, notes, and next steps together.
              </p>
            </div>
            <button onClick={() => setAdding(true)}>
              Register your first project
            </button>
          </main>
        ) : (
          <ProjectWorkspace
            key={selectedProject}
            projectId={selectedProject}
            historyLink={linkedProjectExists ? historyLink : null}
            onLeaveHistory={() => selectProject(selectedProject)}
            onProjectChanged={fetchProjects}
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
  historyLink,
  onLeaveHistory,
}: {
  projectId: number;
  onProjectChanged: () => Promise<void>;
  historyLink: HistoryLink | null;
  onLeaveHistory: () => void;
}) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [selectedView, setView] = useState('Dashboard');
  const view = historyLink ? 'History' : selectedView;
  const [generation, setGeneration] = useState(loadGenerationSettings);
  useEffect(() => {
    try { localStorage.setItem('projecthub.ai-settings', JSON.stringify(generation)); } catch { /* Storage is optional. */ }
  }, [generation]);
  const [error, setError] = useState('');
  const [form, setForm] = useState<{
    title: string;
    fields: Field[];
    initial?: any;
    save: (data: any) => Promise<unknown>;
  } | null>(null);
  const [report, setReport] = useState<any>(null);
  const [todoTitle, setTodoTitle] = useState('');
  const [bulkTodos, setBulkTodos] = useState(false);
  const [search, setSearch] = useState('');
  const [results, setResults] = useState<any[] | null>(null);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  const base = `/projects/${projectId}`;
  useEffect(() => {
    if (historyLink) {
      setResults(null);
      setReport(null);
    }
  }, [historyLink]);
  function leaveHistory() {
    onLeaveHistory();
    setView('History');
  }
  const load = useCallback(async () => {
    const result = await api<Overview>(`${base}/overview`);
    if (alive.current) setOverview(result);
  }, [base]);
  useEffect(() => {
    alive.current = true;
    load().catch((e) => setError(e.message));
    return () => {
      alive.current = false;
    };
  }, [load]);
  async function run(action: () => Promise<unknown>) {
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
  function edit(
    title: string,
    fields: Field[],
    endpoint: string,
    initial?: any,
    transform: (d: any) => any = (d) => d,
  ) {
    setForm({
      title,
      fields,
      initial,
      save: async (data) => {
        await (initial ? patch : post)(`${base}${endpoint}`, transform(data));
        await load();
      },
    });
  }
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
  const activeTodos = tasks.filter((task) => task.status !== 'completed');
  const completedTodos = tasks.filter((task) => task.status === 'completed');
  const todoRow = (task: RecordData) => (
    <li key={task.id}>
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
      <EditButton label={`Edit TODO: ${task.title}`} disabled={busy}
        onClick={() => edit('Edit TODO', [{ ...titleField, maxLength: 500 },
          { name: 'description', label: 'Notes', type: 'textarea', rows: 8 }], `/tasks/${task.id}`, task,
          (data) => ({ title: String(data.title).trim(), description: data.description }))} />
      <button type="button" className="icon-edit" aria-label={`Delete TODO: ${task.title}`}
        title="Delete TODO" disabled={busy} onClick={() => {
          if (window.confirm(`Delete TODO “${task.title}”?`)) {
            void run(() => api(`${base}/tasks/${task.id}`, { method: 'DELETE' }));
          }
        }}>
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
          strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" />
        </svg>
      </button>
    </li>
  );
  const tabs = [
    'Dashboard',
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
          <p className="eyebrow">
            PROJECT / {String(project.id).padStart(2, '0')}
          </p>
          <h1>{project.name}</h1>
          <p className="muted">
            {project.description ||
              'Add a description to give this project context.'}
          </p>
        </div>
        <div className="project-actions">
          <Badge value={project.status} />
          <button
            className="secondary"
            onClick={() =>
              setForm({
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
          <GenerationSettings base={base} generation={generation} onChange={setGeneration} />
        </div>
      </div>
      <nav className="tabs" aria-label="Project sections">
        {tabs.map((t) => (
          <button
            key={t}
            className={view === t ? 'active' : ''}
            onClick={() => {
              if (historyLink) onLeaveHistory();
              setView(t);
              setReport(null);
              setResults(null);
            }}
          >
            {t}
          </button>
        ))}
      </nav>
      <main>
        <ErrorBox error={error} />
        <form
          className="search-bar"
          onSubmit={async (e) => {
            e.preventDefault();
            await run(async () => {
              const r = await api(
                `${base}/search?q=${encodeURIComponent(search)}`,
              );
              setResults(Array.isArray(r) ? r : (r.results ?? []));
            });
          }}
        >
          <span aria-hidden="true">⌕</span>
          <input
            aria-label="Search project"
            placeholder="Search this project…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <button className="secondary" disabled={busy || !search.trim()}>
            Search
          </button>
          {results && (
            <button
              type="button"
              className="quiet"
              onClick={() => setResults(null)}
            >
              Clear
            </button>
          )}
        </form>
        {results ? (
          <section className="card">
            <h2>Search results</h2>
            {results.length ? (
              <PagedItems key={search} label="Project search pages">{results.map((r, i) => (
                <article className="list-item" key={i}>
                  <small>{r.entity_type ?? r.kind ?? r.type ?? 'Record'}</small>
                  <h3>{r.title}</h3>
                  <p>{r.snippet ?? r.content ?? r.description}</p>
                </article>
              ))}</PagedItems>
            ) : (
              <Empty>No matching records in this project.</Empty>
            )}
          </section>
        ) : (
          <>
            {view === 'Dashboard' && (
              <>
                <ProjectBrief base={base} generation={generation} />
                <div className="metrics">
                  <div className="metric">
                    <strong>{notes.length}</strong>
                    <span>Notes</span>
                  </div>
                </div>
                <div className="two-column">
                  {(project.goals.trim() || project.repository_path || project.tags.length > 0) && (
                    <section className="card">
                      <h2>Project overview</h2>
                      {project.goals.trim() && (
                        <>
                          <h4>Goals</h4>
                          <Markdown>{project.goals}</Markdown>
                        </>
                      )}
                      {project.repository_path && (
                        <>
                          <h4>Repository</h4>
                          <code className="repository">{project.repository_path}</code>
                        </>
                      )}
                      {project.tags.length > 0 && (
                        <div className="tags">
                          {project.tags.map((tag) => <span key={tag}>{tag}</span>)}
                        </div>
                      )}
                    </section>
                  )}
                  <section className="card">
                    <h2>Latest activity</h2>
                    <PagedItems label="Activity pages">{activity.map((a) => (
                      <div className="activity" key={a.id}>
                        <span className="activity-dot" />
                        <div>
                          <strong>
                            {label(a.action ?? a.event_type ?? 'Updated')}
                          </strong>
                          <p>
                            {a.entity_type ?? ''}{' '}
                            {a.entity_id ? `#${a.entity_id}` : ''}
                          </p>
                          <small>
                            {new Date(a.created_at).toLocaleString()}
                          </small>
                        </div>
                      </div>
                    ))}
                    </PagedItems>
                    {!activity.length && <Empty>No activity recorded.</Empty>}
                  </section>
                </div>
              </>
            )}
            {view === 'History' && (
              <ProjectHistory base={base} projectId={projectId} historyLink={historyLink}
                notes={notes} onLeaveHistory={leaveHistory} />
            )}
            {view === 'AI Q&A' && <ProjectQA base={base} generation={generation} />}
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
                  ? <PagedItems label="Open TODO pages" listClass="todo-list" pageSize={10}>{[...activeTodos].reverse().map(todoRow)}</PagedItems>
                  : <Empty>No open TODOs.</Empty>}
                {!!completedTodos.length && (
                  <details>
                    <summary>Completed ({completedTodos.length})</summary>
                    <PagedItems label="Completed TODO pages" listClass="todo-list" pageSize={10}>{[...completedTodos].reverse().map(todoRow)}</PagedItems>
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
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() =>
                      run(async () => setReport(await api(`${base}/audit`)))
                    }
                  >
                    View audit history
                  </button>
                </div>
                {!notes.length && <Empty>No notes yet.</Empty>}
                <PagedItems label="Note pages">{[...notes].reverse().map((n) => (
                  <section className="card" key={n.id}>
                    <div className="section-title">
                      <h2>{n.title}</h2>
                      <div className="note-actions">
                        <Badge value={n.kind} />
                        <EditButton label={`Edit note: ${n.title}`} disabled={busy}
                          onClick={() =>
                            edit(
                              'Edit note',
                              [
                                titleField,
                                {
                                  name: 'content',
                                  label: 'Markdown content',
                                  type: 'textarea',
                                },
                                { name: 'kind', label: 'Kind' },
                                { name: 'reference', label: 'Reference' },
                              ],
                              `/notes/${n.id}`,
                              n,
                            )
                          }
                        />
                      </div>
                    </div>
                    {isChatGPTShare(n)
                      ? (
                        <details>
                          <summary>Read transcript</summary>
                          <div className="shared-transcript">{n.content}</div>
                        </details>
                      )
                      : <Markdown>{n.content}</Markdown>}
                    {n.reference && (
                      <p>
                        <strong>Reference:</strong>{' '}
                        {isChatGPTShare(n)
                          ? <a href={n.reference} target="_blank" rel="noreferrer">Open shared chat</a>
                          : <span>{n.reference}</span>}
                      </p>
                    )}
                  </section>
                ))}
                </PagedItems>
              </>
            )}
            {view === 'Sources' && (
              <>
                <ChatGPTShares base={base} notes={notes} onImported={load} showHistory={false} />
                <Codex base={base} onError={setError} />
              </>
            )}
            {view === 'Reviews' && (
              <>
                <section className="card">
                  <p className="eyebrow">PROJECT HEALTH</p>
                  <h2>Review follow-ups</h2>
                  <p>
                    Review open TODOs and limitations recorded in your notes.
                  </p>
                  <div className="actions left">
                    <button
                      disabled={busy}
                      onClick={() =>
                        run(async () => setReport(await api(`${base}/review`)))
                      }
                    >
                      Review follow-ups
                    </button>
                  </div>
                </section>
                <AskProject base={base} projectId={projectId} generation={generation} />
                <details>
                  <summary className="card">Continue a Codex discussion</summary>
                  <CodexReview base={base} generation={generation} onNotesChanged={load}
                    onManageSessions={() => { setReport(null); setView('Sources'); }} />
                </details>
              </>
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
          title={form.title}
          fields={form.fields}
          initial={form.initial}
          onSave={form.save}
          onClose={() => setForm(null)}
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

type BriefSource = {
  id: string;
  label: string;
  text: string;
  session_id?: number;
  turn_id?: string;
  decision_id?: number;
  note_id?: number;
  share?: boolean;
  kind?: string;
};
type BriefEntry = { text: string; source_ids: string[] };
type Brief = {
  generated_at: string;
  generation?: GenerationProvenance | null;
  sections: {
    since_last_brief?: BriefEntry[];
    overview: BriefEntry[];
    changes: BriefEntry[];
    architecture?: BriefEntry[];
    open_questions: BriefEntry[];
    decision_candidates: BriefEntry[];
  };
  sources: BriefSource[];
  coverage: string[];
};

function citedSources(sources: BriefSource[], ids: string[]) {
  const byId = new Map(sources.map((source) => [source.id, source]));
  return [...new Set(ids)].flatMap((id) => {
    const source = byId.get(id);
    return source ? [source] : [];
  });
}

function briefReferences(ids: string[], numbers: Map<string, number>, prefix: string) {
  return [...new Set(ids)].map((id) => {
    const number = numbers.get(id);
    if (!number) return null;
    const target = `${prefix}-source-${number}`;
    return (
      <a
        className="brief-reference"
        key={id}
        href={`#${target}`}
        aria-label={`Read source ${number}`}
        onClick={() => document.getElementById(target)?.setAttribute('open', '')}
      >
        [{number}]
      </a>
    );
  });
}

function BriefSources({ sources, prefix, openInNewTab = false }: {
  sources: BriefSource[];
  prefix: string;
  openInNewTab?: boolean;
}) {
  const projectId = Number(prefix.split('-')[0]);
  const linkOptions = openInNewTab ? { target: '_blank', rel: 'noopener' } : {};
  return (
    <ol>
      {sources.map((source, index) => (
        <li key={source.id}>
          <details id={`${prefix}-source-${index + 1}`}>
            <summary>
              {source.turn_id ? `Session #${source.session_id} · Turn ${source.turn_id.slice(0, 8)}…`
                : source.note_id !== undefined
                  ? source.label || `${source.kind === 'ai_note' ? 'AI note' : 'Note'} #${source.note_id}`
                  : source.label}
            </summary>
            {source.turn_id && <small>{source.label}</small>}
            <pre className="json-report">{source.text}</pre>
            {source.session_id !== undefined && source.turn_id && (
              <a {...linkOptions} href={historyTurnHref({ projectId, sessionId: source.session_id, turnId: source.turn_id })}>
                Read full turn
              </a>
            )}
            {source.share && source.note_id !== undefined && (
              <a {...linkOptions} href={chatGPTTranscriptHref({ projectId, noteId: source.note_id })}>Read full transcript</a>
            )}
          </details>
        </li>
      ))}
    </ol>
  );
}

type SavedQA = {
  generated_at: string;
  generation?: GenerationProvenance | null;
  items: { question: string; answer: string; source_ids: string[];
    variants?: { answer: string; source_ids: string[] }[] }[];
  sources: BriefSource[];
};

type Generation = { model: string; effort: string };
type GenerationProps = { base: string; generation: Generation };

function loadGenerationSettings(): Generation {
  try {
    const saved = JSON.parse(localStorage.getItem('projecthub.ai-settings') ?? 'null');
    if (typeof saved?.model === 'string' && typeof saved?.effort === 'string') return saved;
  } catch { /* Storage is optional. */ }
  return { model: '', effort: '' };
}

function generationBody({ model, effort }: Generation) {
  return { model: model || null, effort: effort || null };
}

function AskProject({ base, projectId, generation }: GenerationProps & { projectId: number }) {
  const [question, setQuestion] = useState('');
  const [result, setResult] = useState<{ answer: string; sources: BriefSource[] } | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function ask() {
    const query = question.trim();
    if (!query || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError('');
    setResult(null);
    try {
      const response = await api<{ answer: string; sources: BriefSource[] }>(`${base}/codex/ask`, {
        method: 'POST',
        body: JSON.stringify({ question: query, confirmed: true, ...generationBody(generation) }),
        signal: controller.signal,
      });
      if (!controller.signal.aborted) setResult(response);
    } catch (e) {
      if (!controller.signal.aborted) setError((e as Error).message);
    } finally {
      if (request.current === controller) request.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  const prefix = `${projectId}-ask`;
  const numbers = new Map(result?.sources.map((source, index) => [source.id, index + 1]));
  return (
    <section className="card project-brief" aria-busy={busy}>
      <h2>Ask Project</h2>
      <p className="brief-note">
        ProjectHub searches saved conversations and notes locally. Your question and matching passages
        are sent to OpenAI to generate an answer. The answer is not saved.
      </p>
      <form onSubmit={(event) => { event.preventDefault(); void ask(); }}>
        <label>
          Project question
          <textarea rows={3} required maxLength={2000} value={question} disabled={busy}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="What have we already tried?" />
        </label>
        <button disabled={busy || !question.trim()}>{busy ? 'Asking…' : 'Ask Project'}</button>
      </form>
      {busy && <p role="status">Looking for evidence and preparing an answer…</p>}
      <ErrorBox error={error} />
      {result && (
        <section className="brief-section">
          <h3>Answer</h3>
          <p className="shared-transcript">{result.answer.split(/(\[\d+\])/g).map((part, index) => {
            const id = /^\[(\d+)\]$/.exec(part)?.[1];
            return id && numbers.has(id)
              ? <span key={index}>{briefReferences([id], numbers, prefix)}</span>
              : part;
          })}</p>
          {!!result.sources.length && (
            <div className="brief-sources">
              <h4>Sources</h4>
              <BriefSources sources={result.sources} prefix={prefix} openInNewTab />
            </div>
          )}
        </section>
      )}
    </section>
  );
}

function GenerationSettings({ base, generation, onChange }: GenerationProps & {
  onChange: (settings: Generation) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details className="generation-settings" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="button secondary" aria-label="AI settings" title="AI settings">⚙</summary>
      {open && <GenerationOptions base={base} generation={generation} onChange={onChange} />}
    </details>
  );
}

function GenerationOptions({ base, generation, onChange }: GenerationProps & {
  onChange: (settings: Generation) => void;
}) {
  const [models, setModels] = useState<{ id: string; label: string; efforts: string[] }[]>([]);
  const [status, setStatus] = useState('Loading Codex models…');
  const [accounts, setAccounts] = useState<{ account: string | null; options: { id: string; label: string }[] } | null>(null);
  const [savingAccount, setSavingAccount] = useState(false);
  const [accountError, setAccountError] = useState('');
  const [accountVersion, setAccountVersion] = useState(0);
  useEffect(() => {
    let active = true;
    api<typeof accounts>(`${base}/codex/account`)
      .then((result) => { if (active) setAccounts(result); })
      .catch((e) => { if (active) setAccountError(e.message); });
    return () => { active = false; };
  }, [base]);
  async function changeAccount(account: string) {
    if (savingAccount) return;
    setSavingAccount(true);
    setAccountError('');
    try {
      const result = await patch(`${base}/codex/account`, { account: account || null });
      setAccounts(result);
      setModels([]);
      onChange({ model: '', effort: '' });
      setAccountVersion((value) => value + 1);
    } catch (e) {
      setAccountError((e as Error).message);
    } finally {
      setSavingAccount(false);
    }
  }
  useEffect(() => {
    let active = true;
    api<typeof models>(`${base}/codex/models`)
      .then((result) => {
        if (!active) return;
        setModels(result);
        setStatus(result.length ? '' : 'No models reported by Codex.');
      })
      .catch(() => { if (active) setStatus('Could not load Codex models.'); });
    return () => { active = false; };
  }, [base, accountVersion]);
  const model = models.find((option) => option.id === generation.model);
  const efforts = model?.efforts ?? [];
  return (
    <div className="generation-options">
      <strong>AI settings</strong>
      <label>
        Default account for this project
        <select value={accounts?.account ?? ''} disabled={!accounts || savingAccount}
          onChange={(e) => changeAccount(e.target.value)}>
          <option value="">ProjectHub launch account</option>
          {accounts?.options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
        </select>
      </label>
      <small>Used for AI generation and new sessions. Existing sessions keep their original account.</small>
      {accountError && <small role="alert">{accountError}</small>}
      <label>
        Model
        <select value={generation.model} disabled={savingAccount} onChange={(e) => onChange({ model: e.target.value, effort: '' })}>
          <option value="">Codex default</option>
          {generation.model && !model && <option value={generation.model}>{generation.model}</option>}
          {models.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
        </select>
      </label>
      <label>
        Reasoning effort
        <select value={generation.effort} disabled={!efforts.length}
          onChange={(e) => onChange({ ...generation, effort: e.target.value })}>
          <option value="">Model default</option>
          {generation.effort && !efforts.includes(generation.effort) && <option value={generation.effort}>{generation.effort}</option>}
          {efforts.map((effort) => <option key={effort} value={effort}>{label(effort)}</option>)}
        </select>
      </label>
      {status && <small role="status">{status}</small>}
      {!generation.model && <small>Choose a model to set its reasoning effort.</small>}
    </div>
  );
}

function ProjectQA({ base, generation }: GenerationProps) {
  const [saved, setSaved] = useState<SavedQA | null>(null);
  const [latestVersion, setLatestVersion] = useState<string | null>(null);
  const [revisionId, setRevisionId] = useState<number | null>(null);
  const [revisionLoading, setRevisionLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');
  const [page, setPage] = useState(0);
  const [filter, setFilter] = useState('');
  const path = `${base}/codex/qa`;
  useEffect(() => {
    let active = true;
    api<SavedQA | null>(path)
      .then((result) => {
        if (active) {
          setSaved(result);
          setLatestVersion(result?.generated_at ?? null);
        }
      })
      .catch((e) => { if (active) setError(e.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [path]);
  async function generate() {
    if (loading || generating || revisionLoading) return;
    setGenerating(true);
    setError('');
    try {
      const result = await post(path, { confirmed: true, ...generationBody(generation) });
      setSaved(result);
      setRevisionId(null);
      setLatestVersion(result.generated_at);
      setPage(0);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setGenerating(false);
    }
  }
  const pageSize = 5;
  const terms = filter.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const filtered = (saved?.items ?? []).filter((item) => {
    const text = [item.question, item.answer, ...(item.variants ?? []).map((answer) => answer.answer)].join(' ').toLocaleLowerCase();
    return terms.every((term) => text.includes(term));
  });
  const totalPages = Math.ceil(filtered.length / pageSize);
  const currentPage = Math.min(page, Math.max(0, totalPages - 1));
  const items = filtered.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
  const sources = citedSources(saved?.sources ?? [], items.flatMap((item) =>
    [...item.source_ids, ...(item.variants ?? []).flatMap((answer) => answer.source_ids)]));
  const numbers = new Map(sources.map((source, index) => [source.id, index + 1]));
  return (
    <>
      <section className="card project-brief" aria-busy={loading || generating}>
        <div className="section-title">
          <div>
            <h2>AI Q&A</h2>
            {saved && <small>Saved {new Date(saved.generated_at).toLocaleString()}</small>}
            {saved && <SavedGeneration generation={saved.generation} />}
          </div>
          <button disabled={loading || generating || revisionLoading} onClick={generate}>
            {generating ? 'Generating…' : saved ? 'Refresh Q&A with OpenAI' : 'Generate Q&A with OpenAI'}
          </button>
        </div>
        <p className="brief-note">Questions and answers drawn from your linked conversations and project notes. Generating sends that evidence to OpenAI.</p>
        {generating && <GenerationStatus base={base} kind="qa" />}
        <RevisionHistory<SavedQA> base={base} kind="qa" version={latestVersion}
          disabled={loading || generating} selectedId={revisionId} onLoadingChange={setRevisionLoading}
          onSelect={(snapshot, id) => {
            setSaved(snapshot);
            setRevisionId(id);
            setPage(0);
            if (id === null) setLatestVersion(snapshot?.generated_at ?? null);
          }} />
        {revisionLoading && <p role="status">Loading saved Q&A version…</p>}
        <ErrorBox error={error} />
        {!!saved?.items.length && (
          <label>
            Search all saved Q&A
            <input type="search" value={filter} placeholder="Search questions and answers…"
              onChange={(event) => { setFilter(event.target.value); setPage(0); }} />
            {terms.length > 0 && <small>{filtered.length} of {saved.items.length} questions match</small>}
          </label>
        )}
        {loading && <Empty>Loading saved Q&A…</Empty>}
        {!loading && !error && !items.length && <Empty>{terms.length ? 'No matching saved Q&A.' : saved ? 'No supported answers were found.' : 'No saved Q&A yet.'}</Empty>}
        {totalPages > 1 && (
          <nav className="pagination" aria-label="Q&A pages">
            <button className="secondary" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</button>
            <span role="status">Page {currentPage + 1} of {totalPages}</span>
            <button className="secondary" disabled={currentPage + 1 === totalPages} onClick={() => setPage(currentPage + 1)}>Next</button>
          </nav>
        )}
        {items.map((item) => (
          <article className="brief-section" key={item.question}>
            <h3>{item.question}</h3>
            <p>{item.answer}{briefReferences(item.source_ids, numbers, base.split('/')[2] + '-qa')}</p>
            {!!item.variants?.length && (
              <details>
                <summary>Other recorded answers ({item.variants.length})</summary>
                <p>These sources differ. Review them to determine which answer applies.</p>
                {item.variants.map((answer, index) => (
                  <p key={index}>{answer.answer}{briefReferences(answer.source_ids, numbers, base.split('/')[2] + '-qa')}</p>
                ))}
              </details>
            )}
          </article>
        ))}
      </section>
      {!!sources.length && (
        <section className="card brief-sources" aria-label="Q&A sources">
          <h2>Sources</h2>
          <BriefSources sources={sources} prefix={base.split('/')[2] + '-qa'} />
        </section>
      )}
    </>
  );
}

function ProjectBrief({ base, generation }: GenerationProps) {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [latestVersion, setLatestVersion] = useState<string | null>(null);
  const [revisionId, setRevisionId] = useState<number | null>(null);
  const [revisionLoading, setRevisionLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');
  const request = useRef(0);
  const generatingRef = useRef(false);
  useEffect(() => {
    const id = ++request.current;
    api<Brief | null>(`${base}/codex/brief`)
      .then((result) => {
        if (request.current === id) {
          setBrief(result);
          setLatestVersion(result?.generated_at ?? null);
        }
      })
      .catch((e) => {
        if (request.current === id) setError(e.message);
      })
      .finally(() => {
        if (request.current === id) setLoading(false);
      });
    return () => { request.current += 1; };
  }, [base]);
  async function generate() {
    if (generatingRef.current || loading || revisionLoading) return;
    generatingRef.current = true;
    const id = ++request.current;
    setGenerating(true);
    setError('');
    try {
      const result = await post(`${base}/codex/brief`, { confirmed: true, ...generationBody(generation) });
      if (request.current === id) {
        setBrief(result);
        setRevisionId(null);
        setLatestVersion(result.generated_at);
      }
    } catch (e) {
      if (request.current === id) setError((e as Error).message);
    } finally {
      generatingRef.current = false;
      if (request.current === id) setGenerating(false);
    }
  }
  const sections: [keyof Brief['sections'], string][] = [
    ['since_last_brief', 'Since your last brief'],
    ['overview', 'Overview'],
    ['architecture', 'Architecture'],
    ['changes', 'Recent changes'],
    ['open_questions', 'Open questions'],
  ];
  const sources = citedSources(brief?.sources ?? [], sections.flatMap(([key]) =>
    brief?.sections[key]?.flatMap((entry) => entry.source_ids) ?? [],
  ));
  const sourceNumbers = new Map(sources.map((source, index) => [source.id, index + 1]));
  return (
    <>
      <section className="card project-brief" aria-busy={loading || generating}>
        <div className="section-title">
          <div>
            <h2>Project brief</h2>
            {brief && <small>AI summary · Saved {new Date(brief.generated_at).toLocaleString()}</small>}
            {brief && <SavedGeneration generation={brief.generation} />}
          </div>
          <button disabled={loading || generating || revisionLoading} onClick={generate}>
            {generating ? 'Generating…' : brief ? 'Refresh brief with OpenAI' : 'Generate brief with OpenAI'}
          </button>
        </div>
        <p className="brief-note">
          Generate or refresh to send saved conversations, notes, decisions, architecture, and the previous brief if available to OpenAI.
        </p>
        {generating && <GenerationStatus base={base} kind="brief" />}
        <RevisionHistory<Brief> base={base} kind="brief" version={latestVersion}
          disabled={loading || generating} selectedId={revisionId} onLoadingChange={setRevisionLoading}
          onSelect={(snapshot, id) => {
            setBrief(snapshot);
            setRevisionId(id);
            if (id === null) setLatestVersion(snapshot?.generated_at ?? null);
          }} />
        {revisionLoading && <p role="status">Loading saved brief version…</p>}
        <ErrorBox error={error} />
        {loading && <Empty>Loading saved brief…</Empty>}
        {!loading && !brief && <Empty>No saved brief yet.</Empty>}
        {brief && (
          <>
            {sections.filter(([key]) => brief.sections[key]?.length).map(([key, title]) => (
              <section className="brief-section" key={key}>
                <h3>{title}</h3>
                <ul className="brief-points">
                  {brief.sections[key]?.map((entry, index) => (
                    <li key={index}>
                      {entry.text}
                      {briefReferences(entry.source_ids, sourceNumbers, base.split('/')[2] + '-brief')}
                    </li>
                  ))}
                </ul>
              </section>
            ))}
            {sections.every(([key]) => !brief.sections[key]?.length) && (
              <Empty>The available sources did not support a brief.</Empty>
            )}
          </>
        )}
      </section>
      {brief && (
        <section className="card brief-sources" aria-label="Brief sources">
          <h2>Sources</h2>
          <BriefSources sources={sources} prefix={base.split('/')[2] + '-brief'} />
          <details>
            <summary>What the brief covers</summary>
            <ul>{brief.coverage.map((item, index) => <li key={index}>{item}</li>)}</ul>
          </details>
        </section>
      )}
    </>
  );
}

function ChatGPTShares({ base, notes, onImported, showHistory = true }: {
  base: string;
  notes: RecordData[];
  onImported?: () => Promise<void>;
  showHistory?: boolean;
}) {
  const [adding, setAdding] = useState(false);
  const [page, setPage] = useState(0);
  const shares = notes.filter(isChatGPTShare).sort((a, b) => b.id - a.id);
  const pageCount = Math.max(1, Math.ceil(shares.length / HISTORY_PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const start = currentPage * HISTORY_PAGE_SIZE;
  return (
    <section className="card">
      <div className="section-title">
        <div>
          <h2>ChatGPT shared conversations</h2>
          <p className="muted">{onImported
            ? 'Import a public share link to save its text for the brief and AI Q&A.'
            : 'Saved transcripts. Add conversations in Sources.'}</p>
        </div>
        {onImported && <button className="secondary" onClick={() => setAdding(true)}>Import shared chat</button>}
      </div>
      {adding && onImported && (
        <Form title="Import shared chat"
          fields={[{ name: 'url', label: 'Public ChatGPT share link', required: true }]}
          saveLabel="Import" savingLabel="Importing…" onClose={() => setAdding(false)}
          onSave={async ({ url }) => {
            await post(`${base}/notes/chatgpt-share`, { url: url.trim() });
            await onImported();
            setPage(0);
          }}
        />
      )}
      {!shares.length && <Empty>No shared chats imported.</Empty>}
      {!showHistory && !!shares.length && <p>{shares.length} saved · Browse in History</p>}
      {showHistory && pageCount > 1 && (
        <nav className="pagination" aria-label="Shared chat pages">
          <button className="secondary" disabled={currentPage === 0}
            onClick={() => setPage(currentPage - 1)}>Newer chats</button>
          <span role="status">Page {currentPage + 1} of {pageCount}</span>
          <button className="secondary" disabled={currentPage === pageCount - 1}
            onClick={() => setPage(currentPage + 1)}>Older chats</button>
        </nav>
      )}
      {showHistory && shares.slice(start, start + HISTORY_PAGE_SIZE).map((share) => (
        <details className="history-turn" key={share.id}>
          <summary>{share.title}</summary>
          <p><a href={share.reference} target="_blank" rel="noreferrer">Open shared chat</a></p>
          <div className="shared-transcript">{share.content}</div>
        </details>
      ))}
    </section>
  );
}

function ProjectHistory({ base, projectId, historyLink, notes, onLeaveHistory }: {
  base: string;
  projectId: number;
  historyLink: HistoryLink | null;
  notes: RecordData[];
  onLeaveHistory: () => void;
}) {
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
      <HistorySearch base={base} projectId={projectId}
        onIndexed={() => setIndexVersion((value) => value + 1)} />
      {historyLink && 'noteId' in historyLink ? (
        <IndexedChatGPTTranscript key={`${historyLink.noteId}/${historyLink.query ?? ''}`}
          note={notes.find((note) => note.id === historyLink.noteId && isChatGPTShare(note))}
          query={historyLink.query} onClose={onLeaveHistory} />
      ) : historyLink ? (
        <IndexedHistoryTurn key={`${historyLink.sessionId}/${historyLink.turnId}`}
          prefix={`${base}/codex`} target={historyLink} indexVersion={indexVersion}
          onClose={onLeaveHistory} />
      ) : (
      <>
      <ChatGPTShares base={base} notes={notes} />
      <section className="card">
        <h2>Project conversation history</h2>
        <p className="muted">Browse saved conversations associated with this project. Add sessions in Sources.</p>
        <ErrorBox error={error} />
        {loading ? <Empty>Loading project sessions…</Empty> : (
          <label>
            Project session
            <select value={sessionId} onChange={(e) => setSessionId(e.target.value)}>
              <option value="">Select a session</option>
              {sessions.map((session) => (
                <option key={session.id} value={session.id}>
                  #{session.id} · {session.title || session.thread_id}
                </option>
              ))}
            </select>
          </label>
        )}
        {!loading && !error && !sessions.length && <Empty>No sessions associated with this project.</Empty>}
      </section>
      {sessionId && <SavedHistory key={sessionId} prefix={`${base}/codex`} sessionId={sessionId} searchable />}
      </>
      )}
    </>
  );
}

function IndexedChatGPTTranscript({ note, query, onClose }: {
  note?: RecordData;
  query?: string;
  onClose: () => void;
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
          <div className="shared-transcript">
            {match ? (
              <>
                {content.slice(0, match.start)}
                <mark ref={mark} tabIndex={-1}>{content.slice(match.start, match.start + match.length)}</mark>
                {content.slice(match.start + match.length)}
              </>
            ) : content}
          </div>
        </>
      ) : <ErrorBox error="This ChatGPT transcript is not available in this project." />}
    </section>
  );
}

function IndexedHistoryTurn({ prefix, target, indexVersion, onClose }: {
  prefix: string;
  target: HistoryTurnLink;
  indexVersion: number;
  onClose: () => void;
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
        <div><h2 ref={heading} tabIndex={-1}>Saved conversation turn</h2><small>Session #{target.sessionId} · Turn {target.turnId}</small></div>
        <button className="secondary" onClick={onClose}>Browse recent turns</button>
      </div>
      <ErrorBox error={error} />
      {error && <p>Use Index Codex history or Refresh Codex index above to include newly saved turns, then this link will retry.</p>}
      {loading && <Empty>Loading the requested turn…</Empty>}
      {turn && (
        <>
          <Badge value={turn.status} />
          {turn.messages.map((message, index) => (
            <div className="history-message" key={index}>
              <small>{message.role === 'user' ? 'USER' : 'CODEX'}</small>
              <Markdown>{message.text}</Markdown>
            </div>
          ))}
        </>
      )}
    </section>
  );
}

type SavedTurn = {
  id: string;
  status: string;
  messages: { role: 'user' | 'assistant'; text: string }[];
};

const HISTORY_PAGE_SIZE = 3;

function SavedHistory({
  prefix,
  sessionId,
  searchable = false,
}: {
  prefix: string;
  sessionId: string;
  searchable?: boolean;
}) {
  const [turns, setTurns] = useState<SavedTurn[]>([]);
  const [cursorTrail, setCursorTrail] = useState<(string | null)[]>([null]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const request = useRef(0);

  const load = useCallback(
    async (trail: (string | null)[] = [null]) => {
      const requestId = ++request.current;
      setLoading(true);
      setError('');
      try {
        const cursor = trail[trail.length - 1];
        const query = new URLSearchParams({ limit: String(HISTORY_PAGE_SIZE) });
        if (cursor) query.set('cursor', cursor);
        const page = await api<{
          turns: SavedTurn[];
          next_cursor: string | null;
        }>(`${prefix}/sessions/${sessionId}/history?${query}`);
        if (request.current !== requestId) return;
        setTurns(page.turns);
        setCursorTrail(trail);
        setNextCursor(page.next_cursor);
      } catch (e) {
        if (request.current === requestId) setError((e as Error).message);
      } finally {
        if (request.current === requestId) setLoading(false);
      }
    },
    [prefix, sessionId],
  );

  useEffect(() => {
    load();
    return () => {
      request.current += 1;
    };
  }, [load]);

  const query = filter.trim().toLowerCase();
  const visibleTurns = query
    ? turns.filter((turn) => turn.id.toLowerCase().includes(query)
        || turn.messages.some((message) => message.text.toLowerCase().includes(query)))
    : turns;

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
      {searchable && turns.length > 0 && (
        <label>
          Filter this page
          <input type="search" value={filter} onChange={(event) => setFilter(event.target.value)} />
        </label>
      )}
      {query && !visibleTurns.length && turns.length > 0 && (
        <Empty>No matching turns on this page.</Empty>
      )}
      {visibleTurns.map((turn) => (
        <article className="history-turn" key={turn.id}>
          <div className="section-title">
            <h3>Turn {turn.id}</h3>
            <Badge value={turn.status} />
          </div>
          {turn.messages.map((message, index) => (
            <div className="history-message" key={`${turn.id}-${index}`}>
              <small>{message.role === 'user' ? 'USER' : 'CODEX'}</small>
              <Markdown>{message.text}</Markdown>
            </div>
          ))}
          {!turn.messages.length && (
            <p className="muted">No user or assistant text in this turn.</p>
          )}
        </article>
      ))}
      {!turns.length && !loading && !error && (
        <Empty>No saved conversation turns found.</Empty>
      )}
      {loading && !turns.length && <Empty>Loading saved history…</Empty>}
    </section>
  );
}

function Codex({ base, onError }: {
  base: string;
  onError: (message: string) => void;
}) {
  const [status, setStatus] = useState<any>(null);
  const [sessions, setSessions] = useState<RecordData[]>([]);
  const [sessionId, setSessionId] = useState('');
  const [threadId, setThreadId] = useState('');
  const [sessionQuery, setSessionQuery] = useState('');
  const [sessionFilter, setSessionFilter] = useState('all');
  const [renaming, setRenaming] = useState<RecordData | null>(null);
  const [context, setContext] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const prefix = `${base}/codex`;
  const mounted = useRef(true);
  const available = Boolean(
    status?.installed && status?.app_server && status?.authenticated,
  );
  const selectedSession = sessions.find((s) => String(s.id) === sessionId);
  const readingOnly = selectedSession?.status === 'linked';
  const filteredSessions = [...sessions].filter((session) => {
    const text = [session.title, session.thread_id, session.codex_account, session.status].join(' ').toLocaleLowerCase();
    return text.includes(sessionQuery.trim().toLocaleLowerCase())
      && (sessionFilter === 'all' || (sessionFilter === 'linked'
        ? session.status === 'linked' : session.status !== 'linked'));
  }).sort((a, b) => String(b.last_activity_at ?? b.created_at).localeCompare(String(a.last_activity_at ?? a.created_at))
    || b.id - a.id);
  const refresh = useCallback(async () => {
    const rows = await api<RecordData[]>(`${prefix}/sessions`);
    if (mounted.current) {
      setSessions(rows);
      setSessionId((current) =>
        rows.some((s) => String(s.id) === current)
          ? current
          : String(rows[0]?.id ?? ''),
      );
    }
  }, [prefix]);
  useEffect(() => {
    mounted.current = true;
    Promise.all([api(`${prefix}/status`), refresh()])
      .then(([s]) => {
        if (mounted.current) setStatus(s);
      })
      .catch((e) => {
        if (mounted.current) onError(e.message);
      });
    return () => {
      mounted.current = false;
    };
  }, [prefix, refresh]);
  async function action(fn: () => Promise<unknown>) {
    setBusy(true);
    onError('');
    try {
      await fn();
      await refresh();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <>
      <section className="card">
        <div className="section-title">
          <div>
            <p className="eyebrow">CODEX CONNECTION</p>
            <h2>Manage Codex sessions</h2>
          </div>
          <Badge
            value={
              available ? 'available' : status ? 'unavailable' : 'checking'
            }
          />
        </div>
        <p>
          Sessions use the official local Codex App Server and existing ChatGPT
          authentication. Requests are sent to OpenAI and use your account
          limits. This MVP permits read-only work; code modification approvals
          are unavailable.
        </p>
        {status && (
          <div className="compact-row">
            <p>
              Codex {status.version || 'version unknown'} ·{' '}
              {status.installed ? 'Installed' : 'Not installed'} ·{' '}
              {status.authenticated
                ? 'ChatGPT authentication available'
                : 'Authentication unavailable'}
            </p>
            {status.error && <ErrorBox error={status.error} />}
          </div>
        )}
        <button
          className="secondary"
          disabled={busy}
          onClick={() =>
            action(async () => setStatus(await api(`${prefix}/status`)))
          }
        >
          Check connection
        </button>
      </section>
      <div className="two-column">
        <section className="card">
          <h2>Project sessions</h2>
          <label>
            Find a session
            <input type="search" placeholder="Search title, thread, or account…" value={sessionQuery}
              onChange={(e) => setSessionQuery(e.target.value)} />
          </label>
          <label>
            Sessions
            <select value={sessionFilter} onChange={(e) => setSessionFilter(e.target.value)}>
              <option value="all">All sessions</option>
              <option value="linked">Linked for reading</option>
              <option value="managed">Discussion sessions</option>
            </select>
          </label>
          <PagedItems key={sessionQuery + sessionFilter} label="Project session pages">
            {filteredSessions.map((session) => (
              <article className="list-item" key={session.id}>
                <div className="section-title">
                  <label className="session-choice">
                    <input type="radio" name="source-session" value={session.id} disabled={busy}
                      checked={String(session.id) === sessionId}
                      onChange={() => setSessionId(String(session.id))} />
                    {session.title || `Session #${session.id}`}
                  </label>
                  <EditButton label={`Rename session: ${session.title}`} disabled={busy}
                    onClick={() => setRenaming(session)} />
                </div>
                <small>#{session.id} · {label(session.status ?? '')} · {session.codex_account || 'Launch account'}</small>
                <br />
                <small>Last activity saved here: {new Date(session.last_activity_at ?? session.created_at).toLocaleString()}</small>
                <br />
                <button className="quiet" disabled={busy} onClick={() => void action(() =>
                  patch(`${prefix}/sessions/${session.id}/sync`, { enabled: session.auto_sync === false }))}>
                  {session.auto_sync === false ? 'Resume automatic sync' : 'Pause automatic sync'}
                </button>
              </article>
            ))}
          </PagedItems>
          {!filteredSessions.length && <Empty>{sessions.length ? 'No matching sessions.' : 'No sessions added yet.'}</Empty>}
          {renaming && (
            <Form title="Rename session" fields={[{ ...titleField, maxLength: 200 }]} initial={renaming}
              onClose={() => setRenaming(null)}
              onSave={async ({ title }) => {
                await patch(`${prefix}/sessions/${renaming.id}`, { title: String(title).trim() });
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
              disabled={busy || !available}
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
          <label>
            Existing thread ID (optional)
            <input
              value={threadId}
              onChange={(e) => setThreadId(e.target.value)}
              placeholder="Thread ID to read saved conversation history"
            />
          </label>
          <button
            className="secondary"
            disabled={busy || !threadId.trim()}
            onClick={() =>
              action(async () => {
                const session = await post(`${prefix}/sessions`, {
                  thread_id: threadId.trim(),
                });
                if (mounted.current) setSessionId(String(session.id));
              })
            }
          >
            Add session for reading
          </button>
        </section>
        <section className="card">
          <h2>Context package</h2>
          <p>
            Review the structured project context before submitting a request.
            Each submission records its supplied context version.
          </p>
          <button
            className="secondary"
            disabled={busy}
            onClick={() =>
              action(async () => setContext(await api(`${prefix}/context`)))
            }
          >
            Preview project context
          </button>
          {context && <Report value={context} />}
        </section>
      </div>
    </>
  );
}

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
  const [events, setEvents] = useState<any>(null);
  const [eventsError, setEventsError] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState<{ id: number; sessionId: string; generation: Generation } | null>(null);
  const autoRetried = useRef<number | null>(null);
  const [responsePage, setResponsePage] = useState(0);
  const selectedSession = sessions?.find((session) => String(session.id) === sessionId);
  const canSend = !!selectedSession && selectedSession.status !== 'linked';
  const completed = completedMessages(events?.events ?? []).reverse();
  const responsePageCount = Math.max(1, Math.ceil(completed.length / HISTORY_PAGE_SIZE));
  const currentResponsePage = Math.min(responsePage, responsePageCount - 1);
  const newestResponseId = completed[0]?.id;
  useEffect(() => { setResponsePage(0); }, [sessionId, newestResponseId]);
  const latestRequest = (events?.deliveries ?? []).findLast((item: RecordData) => item.decision_id == null);

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
    setBusy(true);
    setError('');
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function selectReviewSession(id: string) {
    setWaiting(null);
    setEvents(null);
    setEventsError('');
    setSessionId(id);
    try { localStorage.setItem(sessionStorageKey, id); } catch { /* Storage is optional. */ }
  }

  async function ask(question: string, fresh = false) {
    let target = sessionId;
    if (fresh || !canSend) {
      const name = Array.from(question.replace(/\s+/g, ' ').trim());
      const title = name.length > 72 ? name.slice(0, 71).join('').trimEnd() + '…' : name.join('');
      const session: RecordData = await post(`${prefix}/sessions`, { title });
      target = String(session.id);
      setSessions((rows) => [...(rows ?? []), session]);
      selectReviewSession(target);
    }
    const request: RecordData = await post(`${prefix}/sessions/${target}/turns`, {
      prompt: question, ...generationBody(generation),
    });
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
    let alive = true;
    const poll = () =>
      api(`${prefix}/sessions/${sessionId}/events`)
        .then((result) => {
          if (alive) {
            setEvents(result);
            setEventsError('');
          }
        })
        .catch((e) => {
          if (alive) {
            setEventsError(e.message);
          }
        });
    poll();
    const timer = window.setInterval(poll, 3000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [prefix, sessionId]);
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
        {loading ? <p role="status">Loading sessions…</p> : sessions?.length ? (
          <label>
            Review session
            <select value={sessionId} disabled={busy} onChange={(e) => selectReviewSession(e.target.value)}>
              {sessions.map((session) => (
                <option key={session.id} value={session.id}>
                  {session.title ? `${session.title} · #${session.id}` : `Session #${session.id}`}
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
              if (busy || waiting || !question) return;
              void action(() => ask(question));
            }}>
              <label>
                Project question
                <textarea rows={3} required maxLength={20000} value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="What should we do next, based on this project's history?" />
              </label>
              <p className="muted">This sends your question and project context to Codex for a read-only response.</p>
              <button disabled={busy || !!waiting || !prompt.trim()}>
                {busy ? 'Working…' : canSend ? 'Ask Codex' : 'Ask in a new review session'}
              </button>
            </form>
          </details>
        )}
        {waiting && <p role="status">Waiting for Codex to finish. Keep Reviews open to send this question automatically.</p>}
      </section>
      {sessionId && (
        <section className="card">
          <div className="section-title">
            <h2>Saved Codex responses</h2>
            {latestRequest && latestRequest.status !== 'completed' && <Badge value={latestRequest.status} />}
          </div>
          <ErrorBox error={eventsError} />
          {events ? (
            <>
              {completed.slice(currentResponsePage * HISTORY_PAGE_SIZE,
                (currentResponsePage + 1) * HISTORY_PAGE_SIZE).map((message) => (
                <article className="answer" key={message.id}>
                  <div className="section-title">
                    <h3>Codex response</h3>
                    <Badge value="unverified" />
                  </div>
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
                      <small>
                        Context version ID: {item.context_id ?? 'Decision update'}
                      </small>
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
                            <button className="secondary" disabled={busy || !!waiting}
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
                  <summary>Raw event log</summary>
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
