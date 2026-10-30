import { createClient, HTTPError } from "reboop";

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
const client = createClient({
  baseUrl: globalThis.location?.href,
  headers: {
    "Content-Type": "application/json",
    "X-Requested-With": "ThrongDashboard",
  },
});
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
  return sendRequest(path, options, sessionRevision, timeoutMs, readBody);
}

// reboop protects successful body reads. HTTPError exposes a raw Response, so
// bound its optional detail separately and cancel its connection on timeout.
async function errorDetail(response: Response, controller: AbortController, signal: AbortSignal, timeoutMs: number): Promise<string> {
  let cancel = () => {};
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const body = await Promise.race([
      response.json().catch(() => null),
      new Promise<null>(resolve => {
        cancel = () => resolve(null);
        signal.addEventListener("abort", cancel, { once: true });
        if (signal.aborted) cancel();
      }),
    ]);
    return typeof body?.detail === "string" ? body.detail : "";
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
  }
}

async function sendRequest<T>(
  path: string,
  options: RequestInit,
  revision: number,
  timeoutMs: number,
  readBody: ReadBody<T>,
): Promise<{ data: T; total: number | null }> {
  options.signal?.throwIfAborted();
  const controller = new AbortController();
  const signal = AbortSignal.any([controller.signal, ...(options.signal ? [options.signal] : [])]);
  try {
    return await client.read(apiURL(path), async response => ({
      data: (response.status === 204 || options.method?.toUpperCase() === "HEAD"
        ? undefined : await readBody(response)) as T,
      total: response.headers.has("X-Total-Count") ? Number(response.headers.get("X-Total-Count")) : null,
    }), { ...options, signal, credentials: "include", timeoutMs });
  } catch (error) {
    options.signal?.throwIfAborted();
    if (error instanceof HTTPError) {
      if (error.response.status === 401 && path !== "auth/session" && revision === sessionRevision)
        window.dispatchEvent(new Event("session-expired"));
      const detail = await errorDetail(error.response, controller, signal, timeoutMs);
      options.signal?.throwIfAborted();
      throw new ApiError(error.response.status, detail);
    }
    if (error instanceof SyntaxError || error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError(0, "Network error");
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
