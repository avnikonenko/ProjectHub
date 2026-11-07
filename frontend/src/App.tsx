import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import { api, patch, post } from './api';
import { completedMessages } from './codex';
import type { Overview, Project, RecordData } from './types';

type Field = {
  name: string;
  label: string;
  type?: 'text' | 'textarea' | 'select';
  options?: string[];
  required?: boolean;
  maxLength?: number;
};
const titleField: Field = { name: 'title', label: 'Title', required: true };
const descriptionField: Field = {
  name: 'description',
  label: 'Description',
  type: 'textarea',
};
const label = (value: string) => value.replaceAll('_', ' ');
function Badge({ value = '' }: { value?: string }) {
  return <span className={`badge ${value}`}>{label(value)}</span>;
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
function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}

function Form({
  title,
  fields,
  initial = {},
  onSave,
  onClose,
}: {
  title: string;
  fields: Field[];
  initial?: Record<string, any>;
  onSave: (data: Record<string, any>) => Promise<unknown>;
  onClose: () => void;
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
                  rows={4}
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
            <button disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
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
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-icon">P</span>
          <div>
            ProjectHub<small>Research workspace</small>
          </div>
        </div>
        <div className="sidebar-label">YOUR PROJECTS</div>
        <div className="project-list">
          {projects.map((p) => (
            <button
              key={p.id}
              className={`project-option ${p.id === selected ? 'selected' : ''}`}
              onClick={() => setSelected(p.id)}
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
          <span className="local-label">● localhost</span>
        </header>
        <ErrorBox error={error} />
        {loading ? (
          <Empty>Loading projects…</Empty>
        ) : selected === null ? (
          <main>
            <div className="page-heading">
              <p className="eyebrow">YOUR RESEARCH, CONNECTED</p>
              <h1>A clear place for project work.</h1>
              <p>
                Keep questions, evidence, decisions, and notes together.
              </p>
            </div>
            <button onClick={() => setAdding(true)}>
              Register your first project
            </button>
          </main>
        ) : (
          <ProjectWorkspace
            key={selected}
            projectId={selected}
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
            setSelected(p.id);
          }}
        />
      )}
    </div>
  );
}

function PagedRecords({
  records,
  name,
  children,
}: {
  records: RecordData[];
  name: string;
  children: (record: RecordData) => ReactNode;
}) {
  const pageSize = 3;
  const [requestedPage, setPage] = useState(0);
  const totalPages = Math.ceil(records.length / pageSize);
  const page = Math.min(requestedPage, Math.max(0, totalPages - 1));
  const offset = page * pageSize;
  const recent = [...records].sort((a, b) => b.id - a.id).slice(offset, offset + pageSize);
  return (
    <>
      {totalPages > 1 && (
        <nav className="pagination" aria-label={`${name} pages`}>
          <button className="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>
            Newer
          </button>
          <span role="status">Page {page + 1} of {totalPages}</span>
          <button className="secondary" disabled={page + 1 === totalPages} onClick={() => setPage(page + 1)}>
            Older
          </button>
        </nav>
      )}
      {recent.map(children)}
    </>
  );
}

function ProjectWorkspace({
  projectId,
  onProjectChanged,
}: {
  projectId: number;
  onProjectChanged: () => Promise<void>;
}) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [view, setView] = useState('Dashboard');
  const [error, setError] = useState('');
  const [form, setForm] = useState<{
    title: string;
    fields: Field[];
    initial?: any;
    save: (data: any) => Promise<unknown>;
  } | null>(null);
  const [report, setReport] = useState<any>(null);
  const [todoTitle, setTodoTitle] = useState('');
  const [search, setSearch] = useState('');
  const [results, setResults] = useState<any[] | null>(null);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  const base = `/projects/${projectId}`;
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
  const { project, questions, decisions, tasks, notes, activity } = overview;
  const accepted = decisions.filter((d) => d.status === 'accepted');
  const open = questions.filter((q) => q.status !== 'resolved');
  const activeTodos = tasks.filter((task) => task.status !== 'completed');
  const completedTodos = tasks.filter((task) => task.status === 'completed');
  const questionTodos = new Set(tasks.map((task) => task.question_id));
  const decisionTodos = new Set(tasks.map((task) => task.decision_id));
  const todoRow = (task: RecordData) => (
    <li key={task.id}>
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
    </li>
  );
  const tabs = [
    'Dashboard',
    'History',
    'TODO',
    'Notes',
    'Codex',
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
        </div>
      </div>
      <nav className="tabs" aria-label="Project sections">
        {tabs.map((t) => (
          <button
            key={t}
            className={view === t ? 'active' : ''}
            onClick={() => {
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
              results.map((r, i) => (
                <article className="list-item" key={i}>
                  <small>{r.entity_type ?? r.kind ?? r.type ?? 'Record'}</small>
                  <h3>{r.title}</h3>
                  <p>{r.snippet ?? r.content ?? r.description}</p>
                </article>
              ))
            ) : (
              <Empty>No matching records in this project.</Empty>
            )}
          </section>
        ) : (
          <>
            {view === 'Dashboard' && (
              <>
                <ProjectBrief base={base} />
                <div className="metrics">
                  {[
                    [open.length, 'Open questions'],
                    [accepted.length, 'Accepted decisions'],
                    [notes.length, 'Notes'],
                  ].map(([value, name]) => (
                    <div className="metric" key={name}>
                      <strong>{value}</strong>
                      <span>{name}</span>
                    </div>
                  ))}
                </div>
                <div className="two-column">
                  <section className="card">
                    <div className="section-title">
                      <h2>Project overview</h2>
                      <span className="subtle-label">RECORDED FACTS</span>
                    </div>
                    <h4>Goals</h4>
                    <Markdown>{project.goals}</Markdown>
                    <h4>Repository</h4>
                    <code className="repository">
                      {project.repository_path ?? 'No repository registered'}
                    </code>
                    <div className="tags">
                      {project.tags.map((t) => (
                        <span key={t}>{t}</span>
                      ))}
                    </div>
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() =>
                        run(async () => setReport(await api(`${base}/summary`)))
                      }
                    >
                      View structured summary
                    </button>
                  </section>
                  <section className="card">
                    <div className="section-title">
                      <h2>Decisions & follow-ups</h2>
                      <button
                        className="quiet"
                        onClick={() => setView('TODO')}
                      >
                        View TODO →
                      </button>
                    </div>
                    <h3>Open questions</h3>
                    <PagedRecords records={open} name="Open questions">{(q) => (
                      <div className="compact-row" key={q.id}>
                        <h3>{q.title}</h3>
                        <Badge value={q.status} />
                        <button className="quiet todo-source-action" disabled={busy || questionTodos.has(q.id)}
                          onClick={() => run(() => post(`${base}/tasks`, { title: q.title, question_id: q.id }))}>
                          {questionTodos.has(q.id) ? 'Added to TODO' : 'Add to TODO'}
                        </button>
                      </div>
                    )}</PagedRecords>
                    {!open.length && <Empty>No open questions.</Empty>}
                    <h3 className="qa-subheading">Accepted decisions</h3>
                    <PagedRecords records={accepted} name="Accepted decisions">{(d) => (
                      <div className="compact-row" key={d.id}>
                        <h3>{d.title}</h3>
                        <p>{d.description}</p>
                        {d.rationale && <Markdown>{d.rationale}</Markdown>}
                        {d.evidence && <details><summary>Evidence</summary><Markdown>{d.evidence}</Markdown></details>}
                        {d.next_step && (
                          <>
                            <p><strong>Next step:</strong> {d.next_step}</p>
                            <button className="quiet" disabled={busy || decisionTodos.has(d.id)}
                              onClick={() => run(() => post(`${base}/tasks`, { title: d.next_step, decision_id: d.id }))}>
                              {decisionTodos.has(d.id) ? 'Added to TODO' : 'Add next step to TODO'}
                            </button>
                          </>
                        )}
                      </div>
                    )}</PagedRecords>
                    {!accepted.length && (
                      <Empty>
                        Accepted answers and decisions will appear here.
                      </Empty>
                    )}
                  </section>
                  <section className="card">
                    <h2>Latest activity</h2>
                    {activity.slice(0, 8).map((a) => (
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
                    {!activity.length && <Empty>No activity recorded.</Empty>}
                  </section>
                </div>
              </>
            )}
            {view === 'History' && <ProjectHistory base={base} />}
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
                {activeTodos.length
                  ? <ul className="todo-list">{[...activeTodos].reverse().map(todoRow)}</ul>
                  : <Empty>No open TODOs.</Empty>}
                {!!completedTodos.length && (
                  <details>
                    <summary>Completed ({completedTodos.length})</summary>
                    <ul className="todo-list">{[...completedTodos].reverse().map(todoRow)}</ul>
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
                              'technical_assumption',
                              'scientific_assumption',
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
                {notes.map((n) => (
                  <section className="card" key={n.id}>
                    <div className="section-title">
                      <h2>{n.title}</h2>
                      <Badge value={n.kind} />
                    </div>
                    <Markdown>{n.content}</Markdown>
                    {n.reference && (
                      <p>
                        <strong>Reference:</strong> <span>{n.reference}</span>
                      </p>
                    )}
                    <button
                      className="quiet"
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
                    >
                      Edit note →
                    </button>
                  </section>
                ))}
              </>
            )}
            {view === 'Codex' && (
              <Codex
                base={base}
                decisions={accepted}
                onError={setError}
                onNotesChanged={load}
              />
            )}
            {view === 'Reviews' && (
              <section className="card">
                <p className="eyebrow">PROJECT HEALTH</p>
                <h2>Review project records</h2>
                <p>
                  Check unresolved questions, incomplete work, missing evidence,
                  and validation gaps. Deterministic findings reflect stored
                  records; they do not establish scientific validity.
                </p>
                <div className="actions left">
                  <button
                    disabled={busy}
                    onClick={() =>
                      run(async () => setReport(await api(`${base}/review`)))
                    }
                  >
                    Run project review
                  </button>
                  <button
                    className="secondary"
                    onClick={() => setView('Codex')}
                  >
                    Request AI-assisted review →
                  </button>
                </div>
              </section>
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
        {value.findings.map((finding: any, index: number) => (
          <article className="compact-row" key={index}>
            <Badge
              value={finding.category ?? finding.severity ?? 'unverified'}
            />
            {finding.title && <h3>{finding.title}</h3>}
            <p>{finding.message ?? finding.detail}</p>
          </article>
        ))}
        {!value.findings.length && <Empty>No rule-based findings.</Empty>}
        {value.limitations?.map((text: string) => (
          <p key={text}>{text}</p>
        ))}
      </>
    );
  if (Array.isArray(value))
    return (
      <>
        {value.map((item: any, index: number) => (
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
        {!value.length && <Empty>No records.</Empty>}
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
};
type BriefEntry = { text: string; source_ids: string[] };
type Brief = {
  generated_at: string;
  sections: {
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

function BriefSources({ sources, prefix }: { sources: BriefSource[]; prefix: string }) {
  return (
    <ol>
      {sources.map((source, index) => (
        <li key={source.id}>
          <details id={`${prefix}-source-${index + 1}`}>
            <summary>{source.turn_id ? `Session #${source.session_id} · Turn ${source.turn_id.slice(0, 8)}…` : source.label}</summary>
            {source.turn_id && <small>{source.label}</small>}
            <pre className="json-report">{source.text}</pre>
          </details>
        </li>
      ))}
    </ol>
  );
}

function ProjectBrief({ base }: { base: string }) {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');
  const request = useRef(0);
  const generatingRef = useRef(false);
  useEffect(() => {
    const id = ++request.current;
    api<Brief | null>(`${base}/codex/brief`)
      .then((result) => {
        if (request.current === id) setBrief(result);
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
    if (generatingRef.current || loading) return;
    generatingRef.current = true;
    const id = ++request.current;
    setGenerating(true);
    setError('');
    try {
      const result = await post(`${base}/codex/brief`, { confirmed: true });
      if (request.current === id) setBrief(result);
    } catch (e) {
      if (request.current === id) setError((e as Error).message);
    } finally {
      generatingRef.current = false;
      if (request.current === id) setGenerating(false);
    }
  }
  const sections: [keyof Brief['sections'], string][] = [
    ['overview', 'Overview'],
    ['architecture', 'Architecture'],
    ['changes', 'Recent changes'],
    ['open_questions', 'Open questions'],
    ['decision_candidates', 'Possible decisions to review'],
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
          </div>
          <button disabled={loading || generating} onClick={generate}>
            {generating ? 'Generating…' : brief ? 'Refresh brief with OpenAI' : 'Generate brief with OpenAI'}
          </button>
        </div>
        <p className="brief-note">
          Generate or refresh to send saved conversation text from linked sessions, accepted decisions, and saved architecture to OpenAI.
        </p>
        {generating && <p role="status">Generating the brief. A first run over a long history may take a while.</p>}
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
                      {briefReferences(entry.source_ids, sourceNumbers, 'brief')}
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
          <BriefSources sources={sources} prefix="brief" />
          <details>
            <summary>What the brief covers</summary>
            <ul>{brief.coverage.map((item, index) => <li key={index}>{item}</li>)}</ul>
          </details>
        </section>
      )}
    </>
  );
}

function ProjectHistory({ base }: { base: string }) {
  const [sessions, setSessions] = useState<RecordData[]>([]);
  const [sessionId, setSessionId] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
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
      <section className="card">
        <h2>Project conversation history</h2>
        <p className="muted">Browse saved conversations associated with this project. Add sessions in Codex.</p>
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

function Codex({
  base,
  decisions,
  onError,
  onNotesChanged,
}: {
  base: string;
  decisions: RecordData[];
  onError: (message: string) => void;
  onNotesChanged: () => Promise<void>;
}) {
  const [status, setStatus] = useState<any>(null);
  const [sessions, setSessions] = useState<RecordData[]>([]);
  const [sessionId, setSessionId] = useState('');
  const [threadId, setThreadId] = useState('');
  const [prompt, setPrompt] = useState('');
  const [events, setEvents] = useState<any>(null);
  const [eventsError, setEventsError] = useState('');
  const [context, setContext] = useState<any>(null);
  const [decisionId, setDecisionId] = useState('');
  const [delivery, setDelivery] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const prefix = `${base}/codex`;
  const mounted = useRef(true);
  const available = Boolean(
    status?.installed && status?.app_server && status?.authenticated,
  );
  const selectedSession = sessions.find((s) => String(s.id) === sessionId);
  const readingOnly = selectedSession?.status === 'linked';
  const canSend = Boolean(selectedSession && !readingOnly);
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
  useEffect(() => {
    setEvents(null);
    setEventsError('');
    setDelivery(null);
  }, [sessionId]);
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
          <div>
            <p className="eyebrow">CODEX CONNECTION</p>
            <h2>Review and discuss with project context</h2>
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
            Associated session
            <select
              aria-label="Associated session"
              value={sessionId}
              onChange={(e) => setSessionId(e.target.value)}
            >
              <option value="">Select a session</option>
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>
                  #{s.id} · {s.thread_id ?? s.codex_thread_id ?? s.status}
                  {s.status === 'linked' ? ' · Reading only' : ''}
                </option>
              ))}
            </select>
          </label>
          {readingOnly && (
            <p className="muted">
              This session is linked for reading only. Resume selected explicitly
              to enable sending tasks and decision updates.
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
      {sessionId ? (
        <SavedHistory key={`${prefix}/${sessionId}`} prefix={prefix} sessionId={sessionId} />
      ) : (
        <section className="card">
          <h2>Saved conversation history</h2>
          <Empty>Select a session to read its saved history.</Empty>
        </section>
      )}
      <section className="card">
        <h2>Submit a read-only task</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (busy || !canSend) return;
            action(async () => {
              await post(`${prefix}/sessions/${sessionId}/turns`, { prompt });
              setPrompt('');
            });
          }}
        >
          <label>
            Task or review request
            <textarea
              rows={4}
              required
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Review this project's accepted decisions and identify missing validation. Present findings as unverified suggestions."
            />
          </label>
          <p className="muted">
            AI output is a proposal requiring verification. It does not change
            accepted decisions.
          </p>
          <button disabled={busy || !canSend || !prompt.trim()}>
            Send task with context
          </button>
        </form>
      </section>
      <section className="card">
        <h2>Send decision to Codex</h2>
        <p>
          Preview an accepted decision update, then explicitly approve sending
          it to the selected session. Busy or unavailable sessions keep updates
          queued locally.
        </p>
        <label>
          Accepted decision
          <select
            value={decisionId}
            onChange={(e) => {
              setDecisionId(e.target.value);
              setDelivery(null);
            }}
          >
            <option value="">Select a decision</option>
            {decisions.map((d) => (
              <option key={d.id} value={d.id}>
                #{d.id} · {d.title}
              </option>
            ))}
          </select>
        </label>
        <button
          className="secondary"
          disabled={busy || !canSend || !decisionId}
          onClick={() =>
            action(async () =>
              setDelivery(
                await post(`${prefix}/deliveries`, {
                  decision_id: Number(decisionId),
                  session_id: Number(sessionId),
                }),
              ),
            )
          }
        >
          Prepare decision update
        </button>
        {delivery && (
          <div className="delivery-preview">
            <div className="section-title">
              <h3>Review update</h3>
              <Badge value={delivery.status} />
            </div>
            <Report value={delivery} />
            {delivery.error && <ErrorBox error={delivery.error} />}
            <button
              disabled={
                busy || !canSend || !['draft', 'queued', 'failed'].includes(delivery.status)
              }
              onClick={() =>
                action(async () =>
                  setDelivery(
                    await post(`${prefix}/deliveries/${delivery.id}/send`, {
                      confirmed: true,
                    }),
                  ),
                )
              }
            >
              Approve and send update
            </button>
          </div>
        )}
      </section>
      <section className="card">
        <div className="section-title">
          <h2>Session events & responses</h2>
          <span className="subtle-label">REFRESHES EVERY 3 SECONDS</span>
        </div>
        <ErrorBox error={eventsError} />
        {events ? (
          <>
            {completedMessages(events.events ?? []).map((message) => (
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
            {(events.deliveries ?? []).map((item: any) => (
              <article className="delivery-preview" key={item.id}>
                <div className="section-title">
                  <h3>Request #{item.id}</h3>
                  <Badge value={item.status} />
                </div>
                <Markdown>{item.content}</Markdown>
                {item.error && <ErrorBox error={item.error} />}
                <small>
                  Context version ID: {item.context_id ?? 'Decision update'}
                </small>
                {['draft', 'queued', 'failed'].includes(item.status) && (
                  <div className="actions left">
                    <button
                      disabled={busy || !canSend}
                      onClick={() =>
                        action(async () => {
                          await post(`${prefix}/deliveries/${item.id}/send`, {
                            confirmed: true,
                          });
                          setEvents(
                            await api(`${prefix}/sessions/${sessionId}/events`),
                          );
                        })
                      }
                    >
                      Approve and retry this request
                    </button>
                  </div>
                )}
                {item.status === 'unverified' && (
                  <p>
                    Receipt is uncertain. Inspect session events before creating
                    any replacement request.
                  </p>
                )}
              </article>
            ))}
            <details>
              <summary>Raw event log</summary>
              <Report value={events} />
            </details>
          </>
        ) : (
          <Empty>
            {sessionId
              ? eventsError
                ? 'Session events could not be loaded. Retrying automatically…'
                : 'Loading session events…'
              : 'Select a session to inspect its events.'}
          </Empty>
        )}
      </section>
    </>
  );
}
