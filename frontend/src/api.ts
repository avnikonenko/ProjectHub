export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set('Content-Type', 'application/json');
  const response = await fetch(`/api${path}`, { ...options, headers });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail =
        typeof body.detail === 'string'
          ? body.detail
          : JSON.stringify(body.detail ?? body);
    } catch {
      /* Preserve HTTP status for non-JSON responses. */
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}
export const post = (path: string, body: unknown = {}) =>
  api(path, { method: 'POST', body: JSON.stringify(body) });
export const patch = (path: string, body: unknown) =>
  api(path, { method: 'PATCH', body: JSON.stringify(body) });

export function recordMutationPath(path: string, record: { [key: string]: unknown }): string {
  return typeof record.created_at === 'string' ? `${path}?${new URLSearchParams({ created_at: record.created_at })}` : path;
}
