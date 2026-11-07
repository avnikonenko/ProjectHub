import { Children, useState } from 'react';
import ReactMarkdown from 'react-markdown';

export type Field = {
  name: string;
  label: string;
  type?: 'text' | 'textarea' | 'select';
  options?: string[];
  required?: boolean;
  maxLength?: number;
  rows?: number;
};
export const titleField: Field = { name: 'title', label: 'Title', required: true };
export const descriptionField: Field = {
  name: 'description',
  label: 'Description',
  type: 'textarea',
};
export const label = (value: string) => value === 'ai_note' ? 'AI note' : value.replaceAll('_', ' ');
export function Badge({ value = '' }: { value?: string }) {
  return <span className={`badge ${value}`}>{label(value)}</span>;
}
export function EditButton({ label, onClick, disabled }: {
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
export function Markdown({ children }: { children?: string }) {
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
export function ErrorBox({ error }: { error: string }) {
  return error ? (
    <div className="error" role="alert">
      {error}
    </div>
  ) : null;
}

export function PagedItems({ children, label, listClass, pageSize = 3 }: {
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

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Form({
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
