/** Entry points: `ApiError`, `getJson`, and `writeJson` for workbench HTTP transport. */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly diff: string | null = null,
    readonly details: string | null = null,
  ) {
    super(`${status} ${code}`);
  }
}
export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: 'application/json' },
    cache: 'no-store',
  });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const message =
      body && typeof body === 'object' && 'error' in body && typeof body.error === 'string'
        ? body.error
        : 'request_failed';
    const details =
      body && typeof body === 'object' && 'details' in body && typeof body.details === 'string' ? body.details : null;
    throw new ApiError(response.status, message, null, details);
  }
  return (await response.json()) as T;
}
export async function writeJson<T>(path: string, method: 'PUT' | 'POST', body: object): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-Workbench-Request': '1',
      Accept: 'application/json',
    },
    body: JSON.stringify(body),
    cache: 'no-store',
  });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const message =
      body && typeof body === 'object' && 'error' in body && typeof body.error === 'string'
        ? body.error
        : 'request_failed';
    const diff = body && typeof body === 'object' && 'diff' in body && typeof body.diff === 'string' ? body.diff : null;
    const details =
      body && typeof body === 'object' && 'details' in body && typeof body.details === 'string' ? body.details : null;
    throw new ApiError(response.status, message, diff, details);
  }
  return response.json();
}
