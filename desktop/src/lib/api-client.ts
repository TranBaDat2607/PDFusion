/**
 * Typed HTTP client for the PDFusion sidecar.
 *
 * The sidecar's port + bearer token are discovered by `useSidecar` (the only
 * subscriber to the Rust shell's `sidecar://ready` event) and pushed in here
 * via `setSidecar`. Until that happens, all calls block on `waitForSidecar`.
 */

export interface SidecarInfo {
  port: number;
  token: string;
}

let sidecar: SidecarInfo | null = null;
const waiters: Array<(info: SidecarInfo) => void> = [];

export function setSidecar(info: SidecarInfo) {
  sidecar = info;
  while (waiters.length) waiters.shift()!(info);
}

export function getSidecar(): SidecarInfo | null {
  return sidecar;
}

export function waitForSidecar(): Promise<SidecarInfo> {
  if (sidecar) return Promise.resolve(sidecar);
  return new Promise((resolve) => waiters.push(resolve));
}

// ---------------------------------------------------------------------------
// HTTP wrapper
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    message?: string,
  ) {
    super(message ?? `${status}: ${detail}`);
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  init?: RequestInit,
): Promise<T> {
  const info = await waitForSidecar();
  const url = `http://127.0.0.1:${info.port}${path}`;
  const response = await fetch(url, {
    method,
    headers: {
      Authorization: `Bearer ${info.token}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    ...init,
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const errBody = await response.json();
      detail = errBody.detail ?? detail;
    } catch {
      // body wasn't JSON; keep statusText
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T,>(path: string) => request<T>("GET", path),
  post: <T,>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T,>(path: string, body?: unknown) => request<T>("PUT", path, body),
  delete: <T,>(path: string) => request<T>("DELETE", path),
};

/** Build a fully-qualified URL for the sidecar (e.g. for pdf.js to fetch a file). */
export async function sidecarUrl(path: string): Promise<string> {
  const info = await waitForSidecar();
  return `http://127.0.0.1:${info.port}${path}`;
}

/** Token for callers that need to set their own Authorization header (e.g. EventSource). */
export async function sidecarToken(): Promise<string> {
  const info = await waitForSidecar();
  return info.token;
}
