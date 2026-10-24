export const API_BASE = window.__TMC_API_URL__;
export const apiURL = (path: string) => `${API_BASE}/${path}`;

let sessionRevision = 0;
let sessionQueue: Promise<unknown> = Promise.resolve();

export function advanceSessionRevision() {
  ++sessionRevision;
}

function withSessionLock<T>(run: () => Promise<T>): Promise<T> {
  // Cookie writes are shared by tabs. Finish an old logout before a new login
  // can issue its cookie, including when the requests return out of order.
  if (navigator.locks) return navigator.locks.request(`tmc-session:${API_BASE}`, run);
  const pending = sessionQueue.then(run, run);
  sessionQueue = pending.catch(() => {});
  return pending;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}
export async function request<T>(
  path: string,
  options: RequestInit = {},
  timeoutMs = 20_000,
): Promise<{ data: T; total: number | null }> {
  const method = (options.method ?? "GET").toUpperCase();
  const changesCookie = path === "auth/session" && (method === "POST" || method === "DELETE")
    || method === "DELETE" && /^users\/[\da-f-]{36}$/i.test(path);
  if (changesCookie) return withSessionLock(() => sendRequest<T>(path, options, ++sessionRevision, timeoutMs));
  return sendRequest<T>(path, options, sessionRevision, timeoutMs);
}

async function sendRequest<T>(
  path: string,
  options: RequestInit,
  revision: number,
  timeoutMs: number,
): Promise<{ data: T; total: number | null }> {
  let response: Response;
  try {
    response = await fetch(apiURL(path), {
      ...options,
      signal: AbortSignal.any([
        AbortSignal.timeout(timeoutMs),
        ...(options.signal ? [options.signal] : []),
      ]),
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "ThrongDashboard",
        ...options.headers,
      },
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError(0, "Network error");
  }
  if (!response.ok) {
    if (response.status === 401 && path !== "auth/session" && revision === sessionRevision)
      window.dispatchEvent(new Event("session-expired"));
    let detail = "";
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : "";
    } catch {}
    throw new ApiError(response.status, detail);
  }
  const data = response.status === 204 ? undefined : await response.json();
  return {
    data: data as T,
    total: response.headers.has("X-Total-Count")
      ? Number(response.headers.get("X-Total-Count"))
      : null,
  };
}
export const api = async <T>(path: string, options?: RequestInit, timeoutMs?: number) =>
  (await request<T>(path, options, timeoutMs)).data;
export const json = (method: string, data?: unknown): RequestInit => ({
  method,
  body: data === undefined ? undefined : JSON.stringify(data),
});
export async function allPages<T>(
  path: string,
  signal?: AbortSignal,
  pageSize = 200,
): Promise<T[]> {
  const rows: T[] = [];
  for (let offset = 0; ; offset += pageSize) {
    const page = await api<T[]>(
      `${path}${path.includes("?") ? "&" : "?"}limit=${pageSize}&offset=${offset}`,
      { signal },
    );
    rows.push(...page);
    if (page.length < pageSize) return rows;
  }
}
