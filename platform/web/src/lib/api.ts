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
const DEFAULT_TIMEOUT_MS = 20_000;
type ReadBody<T> = (response: Response) => Promise<T>;

export function request<T>(
  path: string,
  options: RequestInit = {},
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<{ data: T; total: number | null }> {
  return requestWithReader<T>(path, options, timeoutMs, response => response.json());
}

export async function apiBlob(path: string, options: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<Blob> {
  return (await requestWithReader(path, options, timeoutMs, response => response.blob())).data;
}

async function requestWithReader<T>(
  path: string,
  options: RequestInit,
  timeoutMs: number,
  readBody: ReadBody<T>,
): Promise<{ data: T; total: number | null }> {
  const method = (options.method ?? "GET").toUpperCase();
  const changesCookie = path === "auth/session" && (method === "POST" || method === "DELETE")
    || method === "DELETE" && /^users\/[\da-f-]{36}$/i.test(path);
  if (changesCookie) return withSessionLock(() => sendRequest(path, options, ++sessionRevision, timeoutMs, readBody));
  const revision = sessionRevision;
  if (method !== "GET" && method !== "HEAD") return sendRequest(path, options, revision, timeoutMs, readBody);
  // Only reads can be replayed when the response was lost. Keep the hostname so
  // DNS and connection selection remain with the browser on every attempt.
  let delay = 1_000;
  for (;;) {
    try {
      return await sendRequest(path, options, revision, timeoutMs, readBody);
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 0) throw error;
      await waitForRetry(Math.min(delay + Math.random() * 250, 10_000), options.signal);
      delay = Math.min(delay * 2, 10_000);
    }
  }
}

function waitForRetry(delay: number, signal?: AbortSignal | null): Promise<void> {
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", cancel);
      resolve();
    }, delay);
    const cancel = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", cancel);
      reject(signal?.reason);
    };
    signal?.addEventListener("abort", cancel, { once: true });
  });
}

async function sendRequest<T>(
  path: string,
  options: RequestInit,
  revision: number,
  timeoutMs: number,
  readBody: ReadBody<T>,
): Promise<{ data: T; total: number | null }> {
  options.signal?.throwIfAborted();
  const deadline = new AbortController();
  const timer = setTimeout(() => deadline.abort(new DOMException("Timed out", "TimeoutError")), timeoutMs);
  try {
    const response = await fetch(apiURL(path), {
      ...options,
      signal: AbortSignal.any([
        deadline.signal,
        ...(options.signal ? [options.signal] : []),
      ]),
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "ThrongDashboard",
        ...options.headers,
      },
    });
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
    const data = response.status === 204 || options.method?.toUpperCase() === "HEAD"
      ? undefined : await readBody(response);
    return {
      data: data as T,
      total: response.headers.has("X-Total-Count")
        ? Number(response.headers.get("X-Total-Count"))
        : null,
    };
  } catch (error) {
    options.signal?.throwIfAborted();
    if (error instanceof ApiError || error instanceof SyntaxError) throw error;
    if (!deadline.signal.aborted && error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError(0, "Network error");
  } finally {
    clearTimeout(timer);
  }
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
